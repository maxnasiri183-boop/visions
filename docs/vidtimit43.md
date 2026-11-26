# Private research gallery and video corpus

## Data sources and eligibility

VidTIMIT contains recordings of 43 volunteers over three capture sessions. The publisher permits research use and distribution under its original notice and requires the Sanderson–Lovell citation in resulting publications. This import retains the publisher's unmodified notice and documentation privately, downloads one archive at a time and verifies each ZIP against its recorded checksum. The source videos depict people speaking and turning their heads in an office. The dataset supports research on cross-session recognition and retrieval. Crowded surveillance scenes, outdoor lighting and cross-camera generalization need separate evidence. [Publisher and license](https://conradsanderson.id.au/vidtimit/) · [Original session documentation](https://conradsanderson.id.au/vidtimit/zips/vidtimit_documentation.pdf) · [Publisher-linked Zenodo release 158963](https://zenodo.org/records/158963)

C. Sanderson and B. C. Lovell. *Multi-Region Probabilistic Histograms for Robust and Scalable Identity Inference*. Lecture Notes in Computer Science 5558, pp. 199–208, 2009.

| Corpus | Evidence and access | Host implications |
|---|---|---|
| VidTIMIT | All 43 subject ZIPs total 3,216,753,724 bytes. The ten existing verified archives account for 741,226,876 bytes. | The incremental download is 2,475,526,848 bytes. The importer reads JPEG members directly and hard-links matching local archives. It preserves the host's 24 GiB pipeline reserve. |
| PaSC | NIST describes 293 people, 9,376 stills and 2,802 videos, with changes in viewpoints, sensors, distance and location. Notre Dame lists a 71 GB distribution. An institutionally authorized signatory must execute the release and return it via institutional email. Approved institutions receive Globus instructions. [NIST challenge](https://www.nist.gov/programs-projects/point-and-shoot-face-recognition-challenge-pasc) · [University download instructions](https://cvrl-web.crc.nd.edu/projects/data/) · [PaSC agreement](https://cvrl.nd.edu/media/django-summernote/2018-09-19/766fd40f-fef3-4ae5-a5ab-1b184e9dc4c3.pdf) | At the initial 76 GiB free, about 52 GiB remained above the operational reserve. A 71 GB transfer alone needs roughly 66 GiB. Access approval and additional storage must precede the full import. |
| IJB-C | NIST lists 3,531 subjects in the benchmark's legal notice and media-specific Creative Commons terms. NIST discontinued distribution in March 2023. [Benchmark legal notice](https://www.nist.gov/system/files/documents/2017/12/26/readme.pdf) · [NIST distribution status](https://www.nist.gov/programs-projects/face-challenges) | The official NIST distribution is unavailable. The 1,000-person accuracy target still requires an authorized large gallery and enough independent negative probes. |
| VGGFace2 | Oxford reports 9,131 identities and 3.31 million web-sourced still images. Its project website has removed the original download links. [Oxford publication](https://robots.ox.ac.uk/~vgg/publications/2018/Cao18/) · [Publisher download status](https://www.robots.ox.ac.uk/~vgg/data/vgg_face2/) | An independently authorized supply would be needed, plus distinct video/session data for the operator workflow. |
| ChokePoint | This is a potential separate multi-camera portal study with 48 sequences and labeled eye positions. The publisher specifies noncommercial research terms and retention of the original license. [Publisher access and terms](https://arma.sourceforge.net/chokepoint/) · [Published archives and checksums](https://zenodo.org/records/815657) | A selected portal sequence could test genuine crowded-footage behavior independently of the studio corpus. ChokePoint is excluded from the measurements here. |

## Private import

Install the runtime and research distributions in the project virtual environment with `.venv/bin/python -m pip install ./apps/operator ./research/identity`. With FFmpeg and ffprobe on PATH, run the importer from the repository root.

```bash
.venv/bin/python -m identity.import_vidtimit \
  --root "$PWD/private/vidtimit43-v1" \
  --pilot-root "$PWD/private/operator"
```

The command creates a separate directory with an enrollment database, immutable manifests, the split plan, source notices, publisher and local checksums, calibration freezes, private per-person evidence and an aggregate quality summary. Interrupting a download retains a partial file for HTTP range resumption. Reruns verify completed archives. Each video is remuxed as original JPEG packets at 25 Hz. The importer verifies every video packet hash and presentation timestamp. `--stage archives` stops at the release ledger. `--stage candidate` continues from a complete ledger. Git ignores the private tree.

## Split and scoring protocol

Each enrolled person contributes one portrait from session 1. The fixed sequence checks `sa1` then `sa2` at original positions 8, 16 and 32, stopping at the first image passing the product’s one-face and quality gates. Each query uses the eighth original image from the session 3 head-turn sequence. The third-session allocation fixes a calibration group of 10 enrolled and seven unenrolled people, and an independent evaluation group of 18 enrolled and eight unenrolled people. The 26 evaluation subjects were absent from the original ten-subject pilot. A person's session 3 images cannot appear in both groups. The actual gallery contains 28 distinct identities and 28 eligible portraits. The original three gallery identities are included, and the original second subject in the two-person demo remains unenrolled.

A second task retrieves appearances in videos even for a person outside the gallery. Thirty-four participants appear in footage and nine are reserved as genuinely absent. Each present person contributes three original session 2 sequences: one head turn and two speech recordings. The search benchmark samples fixed quarter, midpoint and three-quarter positions from each take. Each usable observation requires a single real detector output, five-point alignment and SFace embedding. This sampled, offline exact-search check assesses cross-session photo retrieval at a known index size. The live operator index grows as the new recordings pass through its normal pipeline. Publisher identities and capture sessions provide the labels. This release supplies no frame-by-frame face boxes for a detector recall estimate.

Enrollment and queries use the pinned product model, preprocessing and quality checks. Before any held-out probe is scored, the importer freezes the identification cutoff separately for nested galleries of eight, 16 and 28 people. Each cutoff is the larger of the existing OpenCV baseline and the maximum wrong-person calibration similarity plus 0.000001. Search uses its own cutoff from wrong-person comparisons against the sampled video index. For smaller galleries, evaluation retains only enrolled probes whose person belongs to that gallery and the same eight unenrolled controls. FNIR includes a failed gated identification of an enrolled probe. FPIR counts an incorrect positive result on an unenrolled probe. Confidence intervals use two-sided 95% exact binomial bounds with one query per subject. Repeated video frames do not create extra independent trials.

The private importer creates a 32-byte random alias key with owner-only permissions. The private alias crosswalk maps publisher codes to opaque person keys, display aliases and capture sessions. Preserve the key with the private corpus to reproduce the same aliases. The active gallery exposes neutral aliases such as “Participant 014”, opaque person/session keys, and UUID portrait paths. The private crosswalk and original archive filenames are used only during import and independent scoring. Production receives an opaque recording asset, an acquisition session, a new occurrence ID and the unlabelled image sequence. Neither the publisher identity nor an expected match is sent with a frame. The three predecessor participants retain exactly the same independent reference photographs and embeddings under new active aliases. Prior version links retain the original frozen records.

## Promotion

`catalog.candidate.json` contains opaque asset and acquisition-session IDs, source `studio`, verified SHA-256 digests and neutral media paths. Ground truth stays in private research manifests. Once the candidate results have been reviewed, the following command imports its frozen snapshot into the target operator database and appends the additional distinct media to the existing studio playlist:

```bash
.venv/bin/python -m identity.promote_vidtimit \
  --candidate "$PWD/private/vidtimit43-v1" \
  --target "$PWD/private/operator" --expect-version 2
```

Promotion verifies photo digests and embedding bytes for the three predecessor references and excludes those old keys from future draft publication using the retired_draft_refs table. It installs the 28 opaque references, active membership and current draft-card counts in one SQLite transaction. Historical rejected references remain available in an explicit draft view. The previous versions retain their references and decisions. The importer verifies source files and skips existing catalog checksums. The existing playlist order and source IDs are preserved. Retrying resumes across the database and filesystem boundary. The separate replay source and pinned recordings continue through the same application endpoints.

For a read-only comparison against the gallery and original decisions in pinned live examples, run:

```bash
.venv/bin/python -m identity.vidtimit_demo_check \
  --candidate "$PWD/private/vidtimit43-v1" \
  --pilot-root "$PWD/private/operator"
```

## Measurements

All 43 archives passed their publisher checksums. The 102 verified 512×384 source sequences contain 21,907 original JPEG frames and play for 876.28 seconds at 25 FPS. The media occupies 659,442,519 bytes. Every Matroska packet was checked against the numerically ordered source JPEG and expected timestamp, including the source sequence whose numbering passes 999. Across the 71 still photographs and all video frames, the session, exact and near-duplicate checks found zero cross-role leaks. [Machine-readable measurements](vidtimit43-results.json)

| Gallery size | Calibration known identified | Calibration unknown false matches | Held-out known identified | Held-out unknown false matches | Held-out FPIR, exact 95% CI |
|---:|---:|---:|---:|---:|---|
| 8 | 3/3 | 0/7 | 5/5 | 0/8 | 0%, 0–36.94% |
| 16 | 6/6 | 0/7 | 10/10 | 1/8 | 12.5%, 0.316–52.65% |
| 28 | 10/10 | 0/7 | 18/18 | 1/8 | 12.5%, 0.316–52.65% |

The calibration rule selected the baseline floor of 0.363 at each size. At 28 identities, FNIR was 0/18, with a 95% interval of 0–18.53%. The observed FPIR exceeds the 0.1% target. The corpus contains 43 people, so a 1,000-identity claim requires a separate authorized corpus. All 26 independent evaluation probes passed quality checks. [Machine-readable measurements](vidtimit43-results.json)

Search compared each held-out photograph against embeddings from 306 sampled video frames, using an independently calibrated cutoff of 0.476001. All 22 video-present probes retrieved the correct person at thresholded rank one. All four absent probes returned zero matches above the cutoff, with a 95% absent false-hit interval of 0–60.24%. Twelve of twelve calibration queries for video-present people found the correct person and all five absent controls returned zero hits. Held-out example files for the enrolled, unenrolled-but-present and absent cases are available with neutral filenames in the private candidate's example-photos directory. [Machine-readable measurements](vidtimit43-results.json)

A read-only comparison on five original pinned occurrences scored the already-produced embeddings using the staged membership. It correctly identified all 102 quality-eligible known observations and rejected 140 of 150 quality-eligible unenrolled observations. Ten false accepts illustrate how errors vary across real frames. The other 39 observations lacked eligible embeddings. On the pinned two-person example alone, the candidate correctly matched 92 of 92 eligible known faces and rejected 92 of 100 eligible unenrolled faces. Repeated observations of the same people share capture conditions; independent error-rate estimates come from the held-out photo subjects above. [Machine-readable measurements](vidtimit43-results.json)

For a read-only check of a newly completed, known solo studio observation, run the following from the server after promotion. It verifies the actual recognition decision, event fields, StarRocks count, returned private crop digest, source-frame timestamp and decoded pixels against the original numbered archive member.

```bash
.venv/bin/python -m identity.vidtimit_live_check \
  --candidate "$PWD/private/vidtimit43-v1" \
  --target "$PWD/private/operator" --version 3
```

When the feeder first reads an expanded catalog, it generates each missing browser preview before it schedules from that catalog. The registration timestamps of the first and last new previews were 190.26 seconds apart on this host. Camera scheduling can pause during this first pass. Original material, generated previews and gallery references persist in the private operator data volume.

The first checked v3 studio solo mapped to the expected opaque participant at cosine 0.94851. Its stored event had one matching StarRocks row. The returned crop matched the stored hash and source-frame crop pixel for pixel. The returned full frame differed from the original archive JPEG by a mean of 0.8125 intensity levels on the 0–255 scale. The occurrence was pinned for future browser inspection.
