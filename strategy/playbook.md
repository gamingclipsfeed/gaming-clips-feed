# Gaming Clips Feed — growth playbook (read + update every Sunday run; posting runs use the times here)

## Mission (Shraga, Nov 18 2026)
"Adjust posting styles, videos, edits, hashtags and everything according to what works and what doesn't — play around with it until I get 1 million views on a video."
So every Sunday run OWNS the strategy and must actually change things, not just report:
1. Pull last 2 weeks per post from Metricool (IG reels: views, avg watch, skip rate, shares, saves; TikTok: views, avg watch, full-watch rate, For You %; YouTube: views, avg view duration). Fill the per-post log below.
2. Score each post: views first, then watch % and shares. Group by game, style, length, hook type, posting time, hashtags, caption CTA.
3. KEEP what beats the median, DROP what's below it after 2+ posts. Edit config.json to apply it: `styles` (winning look names), `length_variants`, GTA 6 `hooks` (remove losers, add 3 new ones like the winners), `hashtags` order, and posting time in this file.
4. Always run 1 bold new experiment per week (new hook type, new look, new length, new time slot, new caption format, a series "Part 2", trending GTA 6 moment). Log it in the Experiments table.
5. When a post beats 2x the median: make more like it next week (same game/moment type/hook pattern/look), and post a "Part 2" if possible.
6. Free only, PG-13, no Saturday, no reposting the same video, credit line always kept.

## Goal
Max views, watch time and follows on Instagram, TikTok and YouTube Shorts. 5 posts/week (Sun–Thu), 1/day, within Metricool free 20/month.

## Baseline (first week, Nov 16–18 2026)
- IG: GTA 5 clip 125 views, avg watch 8.6 s of a 38 s clip, 35% skipped in first 3 s. CoD clip 0 views. No likes/shares/saves.
- TikTok: 0 views on all 4 posts (new account; watch whether distribution starts).
- YouTube Shorts: no data yet.
- Lesson: clips too long for the watch time; generic "craziest clip today" hook; posted at 11:00 Israel (≈4 AM US).

## Data check Nov 18 (evening)
- IG: GTA 5 125 views / 8.6 s avg watch of 38 s (23%), 35% skip in first 3 s. CoD 0 views. 0 likes/shares/saves.
- TikTok: distribution started — 21–38 views per post (was 0). Still 0 likes.
- YouTube Shorts: no stats yet.
- Generic "The craziest <game> clip today" hook = weakest. Specific hooks ("How did this even happen?!", "defense had no answer") replace it.
- Metricool best times (Israel): TikTok peaks 10:00 and 18:00; Instagram peaks 10:00–12:00 and 18:00. 18:00 is the strongest slot shared by both (also US morning).

## Current setup (changed Nov 18 evening)
- GTA 6 FIRST until Dec 18 (launch push). Other games only when no usable GTA 6 clip exists.
- Clip length 15 s (was 22 s), action starts 3 s in (was 6 s) → beat the 35% early skip, push completion over 50%.
- Posting time: 18:00 Israel Sun–Thu on all 3 platforms. Test 10:00 on 1 day/week; switch if it wins on 2+ posts.
- Hashtags (max 5): 3 game tags first, then #gaming #shorts (game tags were being cut by the 5-tag cap).
- Each weekly batch now A/B tests automatically: every clip gets a different look (style name saved in its .json) and lengths rotate 12/15/20 s (`length` in .json). Log style + length per post so winners can be picked.
- Every caption ends with an easy reply prompt (rate 1–10, 🔥 emoji); Instagram gets a first comment question.
- Clip length 22 s, action 6 s after start (was 38 s / 12 s) → aim for higher completion rate.
- Posting time: test US-friendly evening slots in Israel time (US afternoon/evening). Week 1 test: 19:00 and 21:00 Israel alternating.
- Hooks: specific, curiosity-driven, game named in the first words ("GTA 6 cops did WHAT 😳"), never the same hook twice in a week.
- Hashtags: 3–5 max — 2-3 game-specific + #gaming + #shorts (YT) / #fyp only on TikTok.

## Experiments log
| week | change | result | keep/revert |
|---|---|---|---|
| Nov 22 | 15 s clips, 18:00 slot, GTA 6 first, game-first hashtags | judge Nov 29 vs Nov 16–18 baseline | — |

## Per-post log
| date | platform | time | game | hook | length s | views | avg watch s | retention/skip | likes | shares | follows |
|---|---|---|---|---|---|---|---|---|---|---|---|
