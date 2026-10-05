# SC4016 Group Project: ransomwatch

Fork of [joshhighet/ransomwatch](https://github.com/joshhighet/ransomwatch) (original README: [.github/UPSTREAM_README.md](.github/UPSTREAM_README.md)).

## Usage

```bash
# 1. Start Tor. Either the project's sidecar container:
sudo docker run -d --name tor -p 9050:9050 ghcr.io/joshhighet/torsocc:latest
#    or your local daemon:  sudo systemctl start tor
# check it works (should print an IsTor:true JSON line)
curl --socks5-hostname 127.0.0.1:9050 https://check.torproject.org/api/ip

# 2. Build the image. Rebuild after any code or requirements.txt change (e.g. Pillow, needed by rw proofs).
sudo docker build -t ransomwatch .

# 3. Make sure the output folder exists
mkdir -p data

# 4. Helper so you don't retype the mounts every time
rw() { sudo docker run --rm --network host \
  -v "$PWD/groups.json:/groups.json" \
  -v "$PWD/posts.json:/posts.json" \
  -v "$PWD/source:/source" \
  -v "$PWD/data:/data" \
  -v "$PWD/docs:/docs" \
  ransomwatch "$@"; }

rw scrape     # fetch every live leak site over Tor → source/*.html, updates groups.json
rw parse      # legacy parsers → posts.json, then builds the structured table → data/
rw markdown   # optional: regenerates the docs/ site pages and graphs

rw victims    # rebuild data/*.csv (victims and history) from the saved pages and cached posts without re-scraping
rw backfill --since 2026-01-01   # walk incransom/qilin/play/safepay/akira history back to a date + fetch post pages (cached in source/detail/)
rw backfill --name akira,play   # backfill only the named groups (comma separated)
rw backfill --since 2023-01-01 --pages 400   # deep history: posts before 2026 go to data/history.json/.csv, the 2026 table is unchanged
rw listings   # read the leak file indexes of published safepay/qilin posts → data types (see "Leak listings" below)
rw listings --name safepay   # only the named groups
rw proofs     # hash every qilin/incransom proof image, plus up to 15 images per safepay leak index (kept in memory only) → proof_sha256/proof_dhash + data/proof_distances.csv
rw add --name <group> --location http://<address>.onion   # track a new site
```

## Coverage by group

What each leak site publishes, and so which columns can be filled. "No" means the site doesn't publish it, not that the scraper misses it.

| | akira | incransom | play | qilin | safepay |
|---|---|---|---|---|---|
| leak claim → `data_types` | yes | some posts, plus the `AD Dump` tag | yes, mostly one template | no (`rw listings`) | no (`rw listings`) |
| `data_size` | in claims | some posts | yes | published posts with file stats | rarely |
| `data_files` | no | pasted `dir` listings | no | published posts with file stats | no |
| `revenue_usd` | rarely, in text | yes | no | no | posts before 2026 (`Revenue $X Million`) |
| `employees` | rarely, in text | `Employees:` line on some posts | no | no | sometimes, in text |
| `activity_raw` | no | `Industry:` line on some posts | yes | yes | no |
| proof images | no | yes | no | yes | sampled from the leak index (`rw proofs`) |
| `encrypted` | no | yes (tag) | no | no | no |

## Output fields

Lists are joined with `;`. An empty cell means the value wasn't available. It doesn't mean zero.

### `data/victims.csv` and `data/history.csv`

One row per victim post. Both files have the same columns. `victims.csv` holds posts dated `2026-01-01` or later. `history.csv` holds older posts and is only filled by a deep `rw backfill --since <date before 2026>`. Posts with no date on the leak site are dropped.

| field | meaning |
|---|---|
| `group` | ransomware group (name from `groups.json`) |
| `victim` | victim name as posted by the group |
| `website` | victim's domain, when the site gives one |
| `date` | attack date used for analysis: the site's post date, capped at `first_seen` (a scheduled release date can be in the future) |
| `date_source` | where `date` came from: always `site`, because undated posts are dropped |
| `published` | date the leak site gives for the post (`YYYY-MM-DD`) |
| `first_seen` / `last_seen` | UTC timestamps of the first and latest scrape that saw this post |
| `country` | ISO 3166 alpha-2 code |
| `country_name` | full country name for `country` |
| `country_source` | `site` (given by the group), `tld` (from the website's country domain) or `text` (country named in the description) |
| `activity` | industry, normalised to the project's categories. `Unknown` if nothing matched |
| `activity_raw` | industry text exactly as the site gives it (qilin, play, incransom's `Industry:` line) |
| `activity_source` | `site` (from `activity_raw`) or `keyword` (guessed from the victim name, description and website) |
| `revenue_usd` | revenue the group claims, in USD. Only dollar amounts are kept (no exchange rates); for a range, the lower bound |
| `revenue_band` | `revenue_usd` bucketed: `<$10M`, `$10M-50M`, `$50M-250M`, `$250M-1B`, `>$1B` |
| `revenue_source` | `site` (a revenue field: incransom's API, safepay's `Revenue $X Million` line, incransom's `Revenue:` line) or `text` (a sentence in `description`, e.g. "$7.3 million in revenue") |
| `employees` / `employee_band` | headcount the group gives for the victim; for a range, the lower bound. Band: `<50`, `50-249`, `250-999`, `1,000-4,999`, `5,000+` |
| `employees_source` | `site` (incransom's `Employees:` line) or `text` (a sentence in `description`, e.g. "with over 140 employees"). Filter out `text` if you want only site fields. |
| `data_types` | stolen data categories, e.g. `Financial;Employee / HR`. From keywords in `leak_claim` plus incransom's `AD Dump` tag (`Credentials / IT`); if the group gave neither, from the folder and file names in the leak index (`rw listings`) |
| `data_types_source` | `claim` (the group's claim or tags) or `listing` (the leak index's folder/file names) |
| `listing_entries` | number of folder/file names read from the leak index. Empty means no index was read, not an empty leak |
| `leak_subject` | whose data that is: `Customers`, `Employees`, `Individuals (unspecified)`, `Company`. `Unclassified` if there is a claim but no type matched |
| `data_size` | claimed leak size, e.g. `130 GB` (play, qilin, akira/incransom claims, incransom `Laek:` lines and pasted `dir` listings) |
| `data_files` | claimed file count (qilin, incransom pasted `dir` listings) |
| `proof_count` | number of proof files/screenshots posted (incransom, qilin), or of images sampled from the leak index (safepay) |
| `proof_ids` | identifiers of the proof files as the site names them (qilin photo names, incransom upload ids). These are upload ids, so the same screenshot posted again gets a new id. For safepay, the SHA-1 of each sampled image's URL (the file name itself is never stored) |
| `proof_sha256` | SHA-256 of each proof image, in `proof_ids` order (`rw proofs`). Same value = byte-identical image |
| `proof_dhash` | 64-bit perceptual (difference) hash of each proof image. Same value = the same picture, even if re-saved or resized |
| `proof_source` | `site` (proofs the group posted: qilin, incransom) or `listing` (images sampled from safepay's leak index, not chosen by the group as proof). Filter on it if you only want real proofs |
| `encrypted` | `True` if the site tags the post as encrypted (incransom). Empty means unknown, not "not encrypted" |
| `status` | post state as the site shows it: `published` / `pending` (akira, qilin, safepay), `published full` / `N days before publication` (play), post tags like `Encrypted,Proof,AD%20Dump` (incransom) |
| `views` | view counter shown on the leak site |
| `description` | what the group says about the victim company |
| `leak_claim` | what the group says it stole |
| `leak_claim_generic` | `True` if the same claim text appears on 5 or more of the group's posts, i.e. a template, not a victim-specific claim |
| `post_url` | onion URL of the individual post (open in Tor Browser) |
| `source_url` | onion address of the leak site it was scraped from |

### Leak listings (`rw listings`)

safepay and qilin don't say what they stole, but they publish a browsable index of the stolen files. `rw listings` reads that index for every published post whose page is cached in `source/detail/` (safepay folder links, qilin "Watch data" pages), one level of subfolders deep.

How this stays safe:

- **No leak file is ever downloaded by `rw listings`.** The one exception is `rw proofs`, which hashes up to 15 safepay images per post in memory (see "Proof hashes" below). Archive links (`.rar`, `.zip`, …) are never requested, any response that isn't an HTML/JSON page is dropped unread, and every page is capped at 2 MB.
- **No folder or file name is ever stored.** Names often contain personal data (people's names, patients, employees). Each name is mapped to a data type in memory and thrown away. Only per-type counts are saved, in `source/listing/*.json`. Names are never logged.
- A type is kept only if at least 2 names point to it, and listing types are used only when the group's own claim gives none.
- Requests go through Tor like the rest of the scrape.
- qilin's "Watch data" link redirects to a different file-server mirror on every request, and in testing (Oct 2026) all of them were unreachable. If the first 5 indexes of a group all fail, the rest of that group is skipped for this run. Failed indexes aren't cached, so the next run tries again.

### Proof hashes (`rw proofs`)

Proof IDs can't show a re-posted leak, because a re-upload gets a new ID. `rw proofs` fetches each proof image of qilin (from its thumbnails) and incransom, and hashes it.

- **Images are never saved.** Proofs are usually photos of the stolen documents. Each image is held in memory, hashed and discarded. Only the two hashes are kept, in `source/proofhash/<group>.json` (proof id → `[sha256, dhash]`).
- Responses that aren't images, or are over 10 MB, are skipped.
- Progress is saved every 200 images, so an interrupted run picks up where it stopped. Already-hashed proofs are never fetched again.
- A full first run takes a few hours: about 8,200 qilin thumbnails (~15 KB each) and about 8,200 full-size incransom images (incransom has no thumbnails). safepay adds about 430 leak indexes and up to about 6,400 images (`rw proofs --name safepay` runs only that part).
- safepay posts no proof images, so `rw proofs` takes up to 15 image files (`.jpg`, `.png`, …) from each published post's leak index and its first level of subfolders and hashes them the same way. The images are picked in sorted URL order, so a rerun samples the same ones. Only the SHA-1 of each image URL is kept as its id, never the file name. `source/proofhash/safepay-posts.json` records which ids belong to which post. These images come from the leak itself, not a proof set the group chose, so they are marked `proof_source = listing`.
- akira and play post no proof images and publish their leaks only as archives (akira by torrent, play as password-protected RARs), so they have no proof hashes. Their archives are never downloaded.

### `data/proof_distances.csv`

Compares the proof images of each post in `victims.csv` (the Jan-Sep 2026 study period) with every other post in `victims` and `history`, from any group. The distance between two images is the number of bits that differ between their 64-bit `proof_dhash` values: 0 means the same picture, and a re-saved or resized picture stays within a few bits. Two images count as close at **4 bits or fewer**.

One row per pair of posts with at least one close image. Rows are sorted by `overlap` (highest first), then `min_distance`. Two 2026 posts that match each other appear twice, once from each side.

| field | meaning |
|---|---|
| `group`, `victim`, `date` | the 2026 post |
| `other_group`, `other_victim`, `other_date` | the post it's compared with |
| `gap_days` | `other_date` minus `date`. Negative means the other post came first |
| `min_distance` | smallest bit difference between any image of the two posts |
| `mean_distance` | for each image of the 2026 post, the distance to its closest image in the other post, averaged |
| `close_images` | images of the 2026 post within 4 bits of an image in the other post |
| `proof_count` / `other_proof_count` | hashed proof images in each post |
| `overlap` | `close_images / proof_count`, from 0 to 1. The best single indicator of a re-post |
| `identical` | byte-identical images the two posts share (`proof_sha256`) |

How to read it: proofs are mostly photos of document pages, which look alike at the hash's 9×8 resolution. A single close image (`close_images` = 1, low `overlap`) is usually a coincidence. A high `overlap` with a low `mean_distance` means the same set of proofs was posted again. Images close to more than 5 posts (banners, near-blank pages) and blank images are ignored.

### `data/victim_data_types.csv` and `data/history_data_types.csv`

Long format: one row per victim per data type. `victim_data_types.csv` is built from `victims.csv`, `history_data_types.csv` from `history.csv`. Victims with no `data_types` have no rows.

| field | meaning |
|---|---|
| `group`, `victim`, `date`, `country`, `activity` | copied from `victims.csv` / `history.csv` |
| `data_type` | one entry from `data_types` |
| `subject` | whose data that type is about (`Customers`, `Employees`, `Individuals (unspecified)`, `Company`) |

