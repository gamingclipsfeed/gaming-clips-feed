#!/usr/bin/env python3
"""
Gaming Clips Feed - automatic clipper.

Every run:
  1. Looks at the newest long videos from the approved creators in config.json
  2. Finds the most-replayed / loudest moments
  3. Cuts each moment into a vertical 9:16 Short with a hook title and captions
  4. Saves finished clips (plus title/description/tags) in ready_to_post/
  5. Keeps a log so nothing is ever clipped twice

Usage:
  python clipper.py            normal daily run
  python clipper.py --check    check that everything is installed
  python clipper.py --test FILE.mp4   make one test Short from a local video
"""
import json, os, re, subprocess, sys, shutil, datetime, random, traceback, math

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "data", "state.json")
WORK = os.path.join(BASE, "data", "work")
READY = os.path.join(BASE, "ready_to_post")
LOGS = os.path.join(BASE, "logs")
for d in (WORK, READY, LOGS):
    os.makedirs(d, exist_ok=True)

# Clips are made the evening before they post: after 3 PM, build for tomorrow.
_now = datetime.datetime.now()
TARGET_DATE = datetime.date.today() + datetime.timedelta(days=1 if _now.hour >= 15 else 0)
TODAY = TARGET_DATE.isoformat()
LOG_FILE = os.path.join(LOGS, f"{TODAY}.log")


def log(msg):
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- tools
def ffmpeg_exe():
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        sys.exit("ffmpeg not found - run install.command again")


FFMPEG = None


def run(cmd, timeout=1800, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(map(str, cmd[:4]))} ...\n{r.stderr[-1500:]}")
    return r


def ytdlp(args, timeout=1800, check=True):
    cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--ffmpeg-location", FFMPEG] + args
    return run(cmd, timeout=timeout, check=check)


def video_duration(path):
    r = run([FFMPEG, "-i", path], check=False)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


# ---------------------------------------------------------------- finding videos
def recent_videos(source, cfg):
    """Newest videos from a creator's channel / playlist URL."""
    n = cfg.get("videos_to_scan_per_creator", 8)
    r = ytdlp(["--flat-playlist", "-J", "--playlist-end", str(n), source["url"]], timeout=300)
    data = json.loads(r.stdout)
    out = []
    for e in data.get("entries") or []:
        if not e or not e.get("id"):
            continue
        out.append({"id": e["id"], "title": e.get("title") or "", "duration": e.get("duration") or 0,
                    "url": e.get("url") if str(e.get("url", "")).startswith("http")
                    else f"https://www.youtube.com/watch?v={e['id']}"})
    return out


def full_info(url):
    r = ytdlp(["-J", "--skip-download", url], timeout=300)
    return json.loads(r.stdout)


# ---------------------------------------------------------------- finding moments
def peaks_from_series(series, duration, k, clip_len, min_gap):
    """series: list of (time_sec, score). Returns up to k peak times, spaced apart."""
    if not series:
        return []
    skip_start, skip_end = 25, 20
    cands = [(t, s) for t, s in series if skip_start <= t <= duration - skip_end - clip_len * 0.5]
    cands.sort(key=lambda x: -x[1])
    chosen = []
    for t, s in cands:
        if all(abs(t - c) >= min_gap for c, _ in chosen):
            chosen.append((t, s))
        if len(chosen) >= k:
            break
    return chosen


def heatmap_moments(info, k, clip_len):
    hm = info.get("heatmap") or []
    if len(hm) < 10:
        return []
    series = [((h["start_time"] + h["end_time"]) / 2, h["value"]) for h in hm]
    return peaks_from_series(series, info.get("duration") or 0, k, clip_len, min_gap=max(90, clip_len * 2))


def loudness_moments(url, vid, duration, k, clip_len):
    """Fallback when YouTube has no 'most replayed' data: find the loudest stretches."""
    audio = os.path.join(WORK, f"{vid}_audio.m4a")
    if not os.path.exists(audio):
        ytdlp(["-f", "worstaudio[ext=m4a]/worstaudio/bestaudio", "-o", audio, url], timeout=1800)
    r = run([FFMPEG, "-i", audio, "-af",
             "aresample=8000,asetnsamples=n=8000,astats=metadata=1:reset=1,"
             "ametadata=print:key=lavfi.astats.Overall.RMS_level", "-f", "null", "-"],
            timeout=1800, check=False)
    levels = []
    for m in re.finditer(r"RMS_level=(-?[\d.]+|-inf)", r.stderr):
        v = m.group(1)
        levels.append(-90.0 if v == "-inf" else float(v))
    try:
        os.remove(audio)
    except OSError:
        pass
    if len(levels) < 60:
        return []
    win = 10
    series = []
    for i in range(0, len(levels) - win):
        series.append((i + win / 2, sum(levels[i:i + win]) / win))
    return peaks_from_series(series, duration or len(levels), k, clip_len, min_gap=max(90, clip_len * 2))


# ---------------------------------------------------------------- captions
_whisper = None


def transcribe(path, cfg):
    """Word-level transcript. Returns list of (start, end, word). Empty list if unavailable."""
    global _whisper
    if not cfg.get("captions", True):
        return []
    try:
        if _whisper is None:
            from faster_whisper import WhisperModel
            _whisper = WhisperModel(cfg.get("whisper_model", "small"), device="cpu", compute_type="int8")
        segs, _ = _whisper.transcribe(path, word_timestamps=True, vad_filter=True)
        words = []
        for s in segs:
            for w in (s.words or []):
                t = w.word.strip()
                if t:
                    words.append((w.start, w.end, t))
        return words
    except Exception as e:
        log(f"  captions skipped: {e}")
        return []


def caption_chunks(words, max_words=3, max_gap=0.6):
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w[0] - cur[-1][1] > max_gap
                    or re.search(r"[.!?]$", cur[-1][2])):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    out = []
    for i, c in enumerate(chunks):
        start = c[0][0]
        end = c[-1][1]
        if i + 1 < len(chunks):
            end = min(max(end, start + 0.4), chunks[i + 1][0][0])
        text = " ".join(x[2] for x in c)
        out.append((start, end, text))
    return out


# ---------------------------------------------------------------- PG filter
BAD_WORDS = {
    "fuck","fucking","fucked","fucker","motherfucker","shit","shitty","bullshit","bitch","bitches",
    "asshole","dick","dicks","pussy","cock","cunt","bastard","slut","whore",
    "nigga","nigger","fag","faggot","retard","retarded","porn","horny",
    "boobs","tits","titties","penis","vagina","wtf","stfu","milf","cocaine",
    "kill yourself","kys","suicide","rape","raped",
}


class NotPG(Exception):
    pass


def pg_check(text):
    t = " " + re.sub(r"[^a-z0-9' ]+", " ", text.lower()) + " "
    hits = [w for w in BAD_WORDS if f" {w} " in t or (len(w) > 4 and w in t.replace(" ", ""))]
    return hits


# ---------------------------------------------------------------- titles
HOOK_TEMPLATES = [
    "{game} players will NOT believe this 😳",
    "This {game} moment is insane 🤯",
    "Wait for it… {game} 💀",
    "Nobody expected this in {game} 😂",
    "The craziest {game} clip today 🔥",
    "How did this even happen?! 😱",
]


def ollama_title(transcript, game, creator):
    try:
        import urllib.request
        prompt = (f"Write ONE short, punchy, family-friendly (PG) YouTube Shorts title (max 8 words, 1 emoji, no hashtags, "
                  f"no quotes) for a {game} gaming clip from {creator}. What is said in the clip: "
                  f"\"{transcript[:600]}\". Reply with the title only.")
        body = json.dumps({"model": os.environ.get("OLLAMA_MODEL", "llama3.2"), "prompt": prompt,
                           "stream": False}).encode()
        req = urllib.request.Request("http://localhost:11434/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
        t = resp.get("response", "").strip().strip('"').splitlines()[0]
        return t[:80] if 3 < len(t) else None
    except Exception:
        return None


def make_title(transcript, game, creator):
    t = ollama_title(transcript, game, creator)
    if t:
        return t
    for sent in re.split(r"(?<=[.!?])\s+", transcript):
        s = sent.strip()
        if s.endswith("!") and 2 <= len(s.split()) <= 8:
            return s[:70] + " 😳"
    return random.choice(HOOK_TEMPLATES).format(game=game)


# ---------------------------------------------------------------- rendering
def find_font():
    for p in ["/System/Library/Fonts/Supplemental/Impact.ttf",
              "/System/Library/Fonts/Supplemental/Arial Black.ttf",
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/Library/Fonts/Arial Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(p):
            return p
    return None


def text_png(text, path, width, font_size, fill, stroke, box=False, upper=True, box_color=(0, 0, 0, 170)):
    from PIL import Image, ImageDraw, ImageFont
    fp = find_font()
    font = ImageFont.truetype(fp, font_size) if fp else ImageFont.load_default()
    if upper:
        text = text.upper()
    # strip emoji/symbols the font can't draw
    text = "".join(ch for ch in text if ord(ch) < 0x2000).strip()
    draw0 = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw0.textlength(test, font=font) <= width - 80:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    lines = lines[:3] or [""]
    lh = int(font_size * 1.18)
    h = lh * len(lines) + 40
    img = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if box:
        maxw = max(d.textlength(l, font=font) for l in lines)
        x0 = (width - maxw) / 2 - 28
        d.rounded_rectangle([x0, 4, width - x0, h - 4], radius=26, fill=tuple(box_color))
    for i, line in enumerate(lines):
        tw = d.textlength(line, font=font)
        d.text(((width - tw) / 2, 20 + i * lh), line, font=font, fill=fill,
               stroke_width=max(4, font_size // 12), stroke_fill=stroke)
    img.save(path)
    return h


# A different look every day (rotates by date)
STYLES = [
    {"name": "Neon Blur", "zoom": 1.3, "bg": "blur", "title_fg": (255, 255, 255), "title_box": (0, 0, 0, 170), "cap": [(255, 230, 0), (255, 255, 255)], "title_y": 250, "cap_y": 1385},
    {"name": "Full Screen", "zoom": 3.2, "bg": "blur", "title_fg": (255, 255, 255), "title_box": (220, 30, 60, 220), "cap": [(255, 255, 255), (0, 255, 220)], "title_y": 180, "cap_y": 1500},
    {"name": "Cinematic", "zoom": 1.0, "bg": "black", "title_fg": (255, 215, 0), "title_box": (0, 0, 0, 0), "cap": [(255, 255, 255), (255, 215, 0)], "title_y": 420, "cap_y": 1330},
    {"name": "Big Zoom", "zoom": 1.8, "bg": "blur", "title_fg": (20, 20, 20), "title_box": (255, 230, 0, 235), "cap": [(255, 230, 0), (255, 255, 255)], "title_y": 230, "cap_y": 1450},
    {"name": "Purple Pop", "zoom": 1.3, "bg": "purple", "title_fg": (255, 255, 255), "title_box": (120, 40, 220, 230), "cap": [(0, 255, 220), (255, 255, 255)], "title_y": 260, "cap_y": 1385},
    {"name": "Arcade", "zoom": 1.5, "bg": "blur", "title_fg": (0, 255, 120), "title_box": (0, 0, 0, 200), "cap": [(0, 255, 120), (255, 255, 255)], "title_y": 220, "cap_y": 1420},
    {"name": "Clean White", "zoom": 1.15, "bg": "white", "title_fg": (15, 15, 15), "title_box": (255, 255, 255, 0), "cap": [(15, 15, 15), (220, 30, 60)], "title_y": 300, "cap_y": 1360},
]


def todays_style():
    return STYLES[TARGET_DATE.toordinal() % len(STYLES)]


def render_short(src, out, title, captions, cfg):
    W, H = 1080, 1920
    st = todays_style()
    zoom = float(st["zoom"])
    tmp = os.path.splitext(out)[0] + "_parts"
    os.makedirs(tmp, exist_ok=True)
    inputs = [src]
    title_png = os.path.join(tmp, "title.png")
    text_png(title, title_png, W, 78, st["title_fg"], (0, 0, 0) if sum(st["title_fg"]) > 300 else (255, 255, 255),
             box=st["title_box"][3] > 0, box_color=st["title_box"])
    inputs.append(title_png)
    cap_pngs = []
    colors = st["cap"]
    for i, (s, e, t) in enumerate(captions):
        p = os.path.join(tmp, f"cap{i:03d}.png")
        text_png(t, p, W, 96, colors[i % 2], (0, 0, 0) if sum(colors[i % 2]) > 300 else (255, 255, 255))
        cap_pngs.append((p, s, e))
        inputs.append(p)

    fg_w = int(W * zoom) // 2 * 2
    if st["bg"] == "blur":
        bgf = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:3,eq=brightness=-0.15[bg]"
    else:
        col = {"black": "black", "purple": "0x2a0f4f", "white": "white"}[st["bg"]]
        bgf = f"color=c={col}:s={W}x{H}:r=30[bgc];[bgc][0:v]scale2ref=w={W}:h={H}[bg][_r];[_r]nullsink"
    fgf = f"[0:v]scale={fg_w}:-2,crop={W}:'min(ih,{H})'[fg]"
    fc = [bgf, fgf, "[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1[v0]",
          f"[v0][1:v]overlay=(W-w)/2:{st['title_y']}[v1]"]
    last = "v1"
    for i, (p, s, e) in enumerate(cap_pngs):
        nxt = f"c{i}"
        fc.append(f"[{last}][{i + 2}:v]overlay=(W-w)/2:{st['cap_y']}:enable='between(t,{s:.2f},{e:.2f})'[{nxt}]")
        last = nxt
    cmd = [FFMPEG, "-y"]
    for p in inputs:
        cmd += ["-i", p]
    cmd += ["-filter_complex", ";".join(fc), "-map", f"[{last}]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-maxrate", "1100k", "-bufsize", "2200k", "-pix_fmt", "yuv420p",
            "-r", "30", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out]
    run(cmd, timeout=1800)
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- one clip
def make_clip(src_file, meta, cfg, state):
    words = transcribe(src_file, cfg)
    caps = caption_chunks(words)
    transcript = " ".join(w[2] for w in words)
    if cfg.get("keep_it_pg", True):
        if not words and cfg.get("captions", True):
            raise NotPG("no transcript to check - skipped to stay safe")
        hits = pg_check(transcript)
        if hits:
            raise NotPG(f"not PG ({', '.join(hits[:3])})")
    title = make_title(transcript, meta["game"], meta["creator"])
    if cfg.get("keep_it_pg", True) and pg_check(title):
        title = random.choice(HOOK_TEMPLATES).format(game=meta["game"])
    n = len([x for x in state["clips"] if x["date"] == TODAY]) + 1
    base = f"{TODAY}_{n:02d}_{re.sub(r'[^A-Za-z0-9]+', '-', meta['game'])[:20]}"
    out = os.path.join(READY, base + ".mp4")
    render_short(src_file, out, title, caps, cfg)
    tags = cfg.get("default_hashtags", ["#shorts", "#gaming"]) + meta.get("hashtags", [])
    desc_lines = [title, "", f"🎮 {meta['game']} | Clip from {meta['creator']}"]
    if meta.get("source_url"):
        desc_lines.append(f"Full video: {meta['source_url']}")
    if meta.get("credit_line"):
        desc_lines.append(meta["credit_line"])
    desc_lines += ["", " ".join(tags)]
    info = {"file": os.path.basename(out), "title": title[:100], "description": "\n".join(desc_lines),
            "tags": [t.lstrip("#") for t in tags], **meta, "date": TODAY, "posted": False}
    save_json(os.path.join(READY, base + ".json"), info)
    state["clips"].append({"date": TODAY, "file": info["file"], "game": meta["game"],
                           "video_id": meta.get("video_id"), "start": meta.get("start")})
    log(f"  ✅ made {info['file']} — {title}")
    return info


def cut_local(src, start, length, out):
    run([FFMPEG, "-y", "-ss", f"{start:.2f}", "-i", src, "-t", f"{length:.2f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", out])


# ---------------------------------------------------------------- daily run
def daily(cfg, state):
    sources = [s for s in cfg.get("sources", []) if s.get("url") and not s.get("paused")]
    if not sources:
        log("No approved creators in config.json yet — nothing to clip. Add campaign creators first.")
        return []
    target = cfg.get("clips_per_day", 5)
    done_today = len([c for c in state["clips"] if c["date"] == TODAY])
    need = target - done_today
    if need <= 0:
        log(f"Already made {done_today} clips today.")
        return []
    clip_len = cfg.get("clip_length_seconds", 35)
    lead = cfg.get("seconds_before_peak", 12)
    min_dur = cfg.get("min_source_minutes", 6) * 60
    per_video = cfg.get("max_clips_per_video", 2)

    # gather candidate moments per game
    by_game = {}
    random.shuffle(sources)
    for src in sources:
        game = src.get("game", "Gaming")
        try:
            vids = recent_videos(src, cfg)
        except Exception as e:
            log(f"⚠️ couldn't read {src.get('creator')}: {str(e)[:300]}")
            continue
        for v in vids:
            if v["duration"] and v["duration"] < min_dur:
                continue
            used = [c for c in state["clips"] if c.get("video_id") == v["id"]]
            if len(used) >= per_video:
                continue
            try:
                info = full_info(v["url"])
            except Exception as e:
                log(f"  skip {v['id']}: {str(e)[:200]}")
                continue
            if src.get("require_cc") and "creative commons" not in str(info.get("license") or "").lower():
                log(f"  skip {v['id']}: not Creative Commons licensed")
                continue
            dur = info.get("duration") or 0
            if dur < min_dur or info.get("is_live") or info.get("live_status") == "is_upcoming":
                continue
            k = per_video - len(used)
            moments = heatmap_moments(info, k + 2, clip_len)
            how = "most-replayed"
            if not moments:
                moments = loudness_moments(v["url"], v["id"], dur, k + 2, clip_len)
                how = "loudest"
            used_starts = [c.get("start", -999) for c in used]
            for t, score in moments:
                start = max(0, t - lead)
                if any(abs(start - u) < clip_len * 2 for u in used_starts):
                    continue
                by_game.setdefault(game, []).append({
                    "video_id": v["id"], "url": v["url"], "start": round(start, 1), "score": score,
                    "views": info.get("view_count") or 0, "how": how, "game": game,
                    "creator": src.get("creator") or info.get("uploader", ""),
                    "hashtags": src.get("hashtags", []),
                    "credit_line": (src.get("credit_line") or "").format(uploader=info.get("uploader", "the creator"), url=f"https://youtu.be/{v['id']}"),
                    "source_title": info.get("title", "")})
            if len(by_game.get(game, [])) >= need * 2:
                break
    for g in by_game:
        by_game[g].sort(key=lambda m: (-m["views"], -m["score"]))

    # round-robin across games so the channel stays varied
    picks, games = [], list(by_game)
    per_vid_count = {}
    want = need * 3  # extra candidates so clips that fail the PG check get replaced
    while len(picks) < want and any(by_game.values()):
        for g in games:
            while by_game[g]:
                m = by_game[g].pop(0)
                c = per_vid_count.get(m["video_id"], 0)
                if c < per_video:
                    per_vid_count[m["video_id"]] = c + 1
                    picks.append(m)
                    break
            if len(picks) >= want:
                break
    log(f"Found {len(picks)} candidate moment(s) in {len(by_game)} game(s); target {need} clip(s).")

    made = []
    for m in picks:
        if len(made) >= need:
            break
        raw = os.path.join(WORK, f"{m['video_id']}_{int(m['start'])}.mp4")
        try:
            log(f"• {m['game']} — {m['creator']} @ {int(m['start'])}s ({m['how']})")
            end = m["start"] + clip_len
            ytdlp(["-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
                   "--download-sections", f"*{m['start']:.1f}-{end:.1f}", "--force-keyframes-at-cuts",
                   "--merge-output-format", "mp4", "-o", raw, m["url"]], timeout=1800)
            if not os.path.exists(raw):
                cands = [f for f in os.listdir(WORK) if f.startswith(os.path.basename(raw)[:-4])]
                if cands:
                    raw = os.path.join(WORK, cands[0])
            meta = {k: m[k] for k in ("game", "creator", "hashtags", "credit_line", "video_id", "start")}
            meta["source_url"] = f"{m['url']}&t={int(m['start'])}s" if "watch?v=" in m["url"] else m["url"]
            made.append(make_clip(raw, meta, cfg, state))
            save_json(STATE_PATH, state)
        except NotPG as e:
            log(f"  🚫 skipped: {e}")
            state["clips"].append({"date": "skipped", "file": "", "game": m["game"],
                                   "video_id": m["video_id"], "start": m["start"]})
        except Exception as e:
            log(f"  ❌ failed: {str(e)[:500]}")
        finally:
            for f in os.listdir(WORK):
                if f.startswith(m["video_id"]):
                    try:
                        os.remove(os.path.join(WORK, f))
                    except OSError:
                        pass
    return made


# ---------------------------------------------------------------- campaign mode
# Campaigns that PROVIDE their own footage (e.g. game studios). We download their
# files once, cut fresh 9:16 edits from them, and add the on-screen text they require.
CAMPAIGN_HOOKS = [
    "Wait for the landing...", "This went VERY wrong", "Nailed it?!",
    "He did NOT stick that", "Hardest trick in the game", "Watch till the end",
    "Physics said NO", "Rate this flip 1-10",
]


def _dl(url):
    return re.sub(r"dl=0", "dl=1", url) if "dropbox.com" in url else url


def campaign_sources(c):
    folder = os.path.join(BASE, "sources", re.sub(r"[^A-Za-z0-9]+", "_", c["name"]))
    os.makedirs(folder, exist_ok=True)
    for i, url in enumerate(c.get("files", [])):
        is_folder = "dropbox.com/scl/fo/" in url or url.endswith(".zip")
        marker = os.path.join(folder, f".done{i:02d}")
        if os.path.exists(marker):
            continue
        log(f"  downloading campaign file {i + 1} for {c['name']}")
        try:
            _fetch(url, i, folder, is_folder)
            open(marker, "w").close()
        except Exception as e:
            log(f"  ⚠️ couldn't download file {i + 1}: {str(e)[:150]}")
    vids = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
            if f.lower().endswith((".mp4", ".mov", ".m4v")) and os.path.getsize(os.path.join(folder, f)) > 1000]
    return vids


def _fetch(url, i, folder, is_folder):
        if is_folder:
            z = os.path.join(folder, f"pack{i:02d}.zip")
            run(["curl", "-fsSL", "-m", "1800", "-o", z, _dl(url)], timeout=1900)
            import zipfile
            try:
                with zipfile.ZipFile(z) as zf:
                    for n in zf.namelist():
                        if n.lower().endswith((".mp4", ".mov", ".m4v")) and not n.startswith("__MACOSX"):
                            dst = os.path.join(folder, f"p{i:02d}_" + re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(n)))
                            with zf.open(n) as fi, open(dst, "wb") as fo:
                                shutil.copyfileobj(fi, fo)
                os.remove(z)
            except zipfile.BadZipFile:
                raise RuntimeError("folder download was not a zip")
        else:
            ext = ".mp4" if not url.lower().split("?")[0].endswith((".wav", ".mp3")) else ".audio"
            dst = os.path.join(folder, f"src{i:02d}{ext}")
            run(["curl", "-fsSL", "-m", "1800", "-o", dst + ".part", _dl(url)], timeout=1900)
            os.replace(dst + ".part", dst)


def norm_piece(src, start, length, out):
    run([FFMPEG, "-y", "-ss", f"{start:.2f}", "-i", src, "-t", f"{length:.2f}",
         "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30",
         "-af", "aresample=48000", "-ac", "2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-c:a", "aac", "-shortest", out])


def build_montage(srcs, target, out):
    """Make a ~target-second clip: one segment from a long source, or several short highlights joined."""
    durs = {s: video_duration(s) for s in srcs}
    longs = [s for s in srcs if durs[s] >= target + 2]
    pieces, used = [], []
    if longs and random.random() < 0.6:
        s = random.choice(longs)
        st = random.uniform(0, durs[s] - target)
        pieces.append((s, st, target)); used.append((s, st))
    else:
        pool = srcs[:]
        random.shuffle(pool)
        total = 0.0
        for s in pool * 3:
            if total >= target:
                break
            d = durs[s]
            seg = min(d, random.uniform(3, 7))
            st = random.uniform(0, max(0, d - seg))
            pieces.append((s, st, seg)); used.append((s, st)); total += seg
    tmp = []
    for k, (s, st, ln) in enumerate(pieces):
        t = out + f".p{k}.mp4"
        norm_piece(s, st, ln, t)
        tmp.append(t)
    lst = out + ".txt"
    with open(lst, "w") as f:
        for t in tmp:
            f.write(f"file '{t}'\n")
    run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])
    for t in tmp + [lst]:
        try:
            os.remove(t)
        except OSError:
            pass
    return used


def campaign_daily(cfg, state):
    made = []
    for c in cfg.get("campaigns", []):
        if c.get("paused"):
            continue
        per_day = c.get("clips_per_day", 2)
        done = len([x for x in state["clips"] if x.get("date") == TODAY and x.get("campaign") == c["name"]])
        if done >= per_day:
            continue
        try:
            srcs = campaign_sources(c)
        except Exception as e:
            log(f"⚠️ {c['name']}: couldn't download footage: {str(e)[:200]}")
            continue
        if not srcs:
            continue
        lo, hi = c.get("clip_seconds", [12, 25])
        hooks_today = [x.get("hook") for x in state["clips"] if x.get("date") == TODAY]
        for _ in range(per_day - done):
            length = random.uniform(lo, hi)
            raw = os.path.join(WORK, f"camp_{random.randint(0, 99999)}.mp4")
            src, start = "", 0.0
            try:
                used_before = {tuple(x) for cl in state["clips"] for x in cl.get("segs", [])}
                used = None
                for _try in range(20):
                    cand = build_montage(srcs, length, raw)
                    segs = [(os.path.basename(a), int(b // 2)) for a, b in cand]
                    if not used_before.intersection(segs):
                        used = cand
                        break
                if used is None:
                    log("  ⛔ not enough NEW footage for another unique clip — skipped so nothing is posted twice")
                    notify("Need more footage: skipped a clip so no video repeats.")
                    break
                src, start = used[0]
                length = video_duration(raw)
                n = len([x for x in state["clips"] if x["date"] == TODAY]) + 1
                base = f"{TODAY}_{n:02d}_{re.sub(r'[^A-Za-z0-9]+', '-', c['name'])[:20]}"
                out = os.path.join(READY, base + ".mp4")
                choices = [h for h in c.get("hooks", CAMPAIGN_HOOKS) if h not in hooks_today] or CAMPAIGN_HOOKS
                hook = random.choice(choices)
                hooks_today.append(hook)
                bottom = c.get("bottom_text", "")
                caps = [(0.0, length, bottom)] if bottom else []
                render_short(raw, out, hook, caps, cfg)
                tags = c.get("hashtags", []) + cfg.get("default_hashtags", [])
                caption = c.get("caption", "{hook}").format(hook=hook) + "\n\n" + " ".join(tags)
                info = {"file": os.path.basename(out), "title": f"{hook} | {c.get('game', c['name'])}"[:100],
                        "description": caption, "campaign": c["name"], "date": TODAY, "posted": False,
                        "submit_at": c.get("submit_url", ""), "notes": c.get("notes", "")}
                save_json(os.path.join(READY, base + ".json"), info)
                state["clips"].append({"date": TODAY, "file": info["file"], "campaign": c["name"],
                                       "source": src, "start": round(start, 1), "game": c.get("game", ""), "hook": hook,
                                       "segs": segs})
                save_json(STATE_PATH, state)
                made.append(info)
                log(f"  ✅ {info['file']} — {hook}")
            except Exception as e:
                log(f"  ❌ campaign clip failed: {str(e)[:300]}")
            finally:
                try:
                    os.remove(raw)
                except OSError:
                    pass
    return made


def cleanup(cfg):
    keep = cfg.get("keep_posted_days", 14)
    cutoff = datetime.date.today() - datetime.timedelta(days=keep)
    for f in os.listdir(READY):
        if f.endswith(".json"):
            info = load_json(os.path.join(READY, f), {})
            try:
                d = datetime.date.fromisoformat(info.get("date", TODAY))
            except ValueError:
                continue
            if info.get("posted") and d < cutoff:
                for ext in (".json", ".mp4"):
                    try:
                        os.remove(os.path.join(READY, f[:-5] + ext))
                    except OSError:
                        pass


def notify(text):
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e",
                        f'display notification "{text}" with title "Gaming Clips Feed"'],
                       capture_output=True)


def main():
    global FFMPEG
    FFMPEG = ffmpeg_exe()
    cfg = load_json(CFG_PATH, {})
    state = load_json(STATE_PATH, {"clips": []})

    if "--check" in sys.argv:
        ok = True
        for mod in ("yt_dlp", "PIL", "faster_whisper"):
            try:
                __import__(mod)
                print(f"OK  {mod}")
            except Exception as e:
                ok = False
                print(f"MISSING  {mod}: {e}")
        print("OK  ffmpeg:", FFMPEG)
        print("Font:", find_font())
        print("Approved creators:", len(cfg.get("sources", [])))
        sys.exit(0 if ok else 1)

    if "--test" in sys.argv:
        src = sys.argv[sys.argv.index("--test") + 1]
        dur = video_duration(src)
        clip_len = min(cfg.get("clip_length_seconds", 35), dur)
        start = max(0, min(dur / 3, dur - clip_len))
        raw = os.path.join(WORK, "test_cut.mp4")
        cut_local(src, start, clip_len, raw)
        meta = {"game": "Test Game", "creator": "Test Creator", "hashtags": ["#test"], "credit_line": "",
                "video_id": "local-test", "start": start}
        state_t = {"clips": []}
        info = make_clip(raw, meta, cfg, state_t)
        os.remove(raw)
        print("Test clip:", os.path.join(READY, info["file"]))
        return

    if "--fetch" in sys.argv:
        for c in cfg.get("campaigns", []):
            log(f"fetch: {c['name']} has {len(campaign_sources(c))} footage files")
        return

    log("===== daily run =====")
    try:
        made = campaign_daily(cfg, state)
        made += daily(cfg, state)
        save_json(STATE_PATH, state)
        cleanup(cfg)
        waiting = len([f for f in os.listdir(READY) if f.endswith(".json")
                       and not load_json(os.path.join(READY, f), {}).get("posted")])
        msg = f"Made {len(made)} new clip(s). {waiting} waiting to post."
        log(msg)
        notify(msg)
    except Exception:
        log("❌ run crashed:\n" + traceback.format_exc())
        notify("Something went wrong today — check the logs folder.")


if __name__ == "__main__":
    main()
