# Private recognition and historical search

The identity worker aligns each recorded detection using its five YuNet landmarks on the retained original video frame. OpenCV SFace produces a 128-dimensional, L2-normalized float32 vector on CPU. The detector's A100 kernel proof covers YuNet. Recognition records expose separate alignment, embedding and gallery lookup times. The exact-search index stores vectors for every quality-eligible observation, including people outside the gallery.

The pinned SFace model comes from OpenCV Zoo at commit `48b10fa505aeb51fbd127fc60a5c660040e823b7`. Its SHA-256 is `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`. The worker checks both model hashes at startup. The preprocessing version is `opencv4.11-alignCrop-5pt-112-L2-v1`. Size, inter-eye spacing, exposure and local-detail checks precede embedding. The initial 0.363 cosine cutoff comes from OpenCV's LFW example. [OpenCV source](https://github.com/opencv/opencv_zoo/tree/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_recognition_sface) · [OpenCV benchmark thresholds](https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html)

## Enrollment and decisions

One person can have multiple eligible reference portraits. Uploads with zero or multiple faces, insufficient size or failed image quality are retained in the draft with the rejection reason. Publication atomically snapshots eligible reference IDs, the model, preprocessing, quality policy, calibration provenance, identification threshold and search threshold. The first recognition decision records the exact version, winning eligible reference and cosine score. Replay of a detection ID preserves that decision after later versions are published. A changed gallery needs its own calibration to make performance claims.

Identity and quality outcomes are separate from detection confidence. A best reference outside the cutoff yields an unknown decision. Poor-quality and failed observations have no usable vector. A detected observation awaiting the worker has a pending state. The gallery exposes the full enrollment portrait and its actual detected face crop.

## Search

The probe endpoint detects every face in an uploaded JPEG or PNG. The selected face supplies the query vector. Source and UTC filters, model/preprocessing compatibility and occurrence retention are applied in SQL before an exact cosine comparison. Time bounds include the start and exclude the end. Eligible candidates below the versioned retrieval cutoff do not become hits. Exact score comparisons run in chunks of 512.

Within each occurrence, hits at most three frame indices apart can join one appearance when their boxes overlap by IoU ≥0.15. Each appearance ranks by its best observation. Results retain its observation list, scores, source, run, occurrence, asset, playout cycle, frame indices, media PTS and observation UTC. Repeated airings have separate occurrence IDs. Grouping is a short-range geometry heuristic and may split a passage when a face turns away. Exact search cost grows with the number of retained candidate vectors. SQLite WAL and the index fit this single-server pilot.

## Prepare and verify the research pilot

Use the operator image's Python environment, or install `numpy==1.26.4 opencv-python-headless==4.11.0.86 Flask Pillow` in a local Python 3.12 environment with FFmpeg. Set `OPERATOR_ROOT` to the absolute private operator-data directory and `IDENTITY_YUNET` to the pinned YuNet file. The application and worker need the same private directory. Use the following commands from the repo root for a **new private research setup**.

```sh
python -m identity.setup --research-vidtimit
python -m identity.pilot
python -m identity.evaluate audit
```

`identity.setup` alone installs only SFace. The research option downloads the 10 subject archives one at a time, checks them against the publisher's archive checksums and retains the original license page and PDF with the data. The output includes five separate private split manifests and source checksums. The empty scene uses eight timestamped CIRA/NOAA GOES-19 GeoColor captures. Keep the private source cache for exact replay after those files leave NOAA's rotating CDN. The quality example deliberately downsamples, blurs or underexposes an independent study stream. The two-person sequence is a spatial composition of two separately recorded moving subjects. Upload examples are still photographs exported from session 3. The multi-face picker combines two existing probes and contributes no extra independent trial.

Start the operator feeder, detector and recognition service. When all original assets have a completed indexed occurrence, run these commands in order.

```sh
python -m identity.evaluate freeze
python -m identity.evaluate measure
python -m identity.extend
```

The extension appends the two genuine, separately recorded session-2 speech takes for every person in the stream. Wait until each clip has one completed indexed occurrence, then run `python -m identity.evaluate extended`. It keeps the original thresholds and records its own expanded leakage audit. Its private report includes expected appearances, the chosen asset-to-occurrence mapping and the actual detection IDs responsible for each result. Frame, identity and thumbnail evidence stay under `OPERATOR_ROOT`. Expiring a feed occurrence removes its indexed vectors and decisions on the next sweep, normally within ten seconds. Uploaded probes are bounded to 100 and expire after 24 hours. Gallery references persist for historical review. The recognition readiness heartbeat is `/tmp/visions-recognition.ready` inside the worker container.

## Measured quality

Version 2 was frozen using one enrolled and two unenrolled calibration people. Of the cutoffs yielding no observed calibration false IDs, the worker selected the highest retaining the maximum correctly identified calibration probes, with OpenCV's threshold as a floor. Historical search used the highest calibration distractor score plus epsilon, with the same floor. The frozen identification cutoff was **0.799078** and the search cutoff **0.374331**. The evaluation then applied them to disjoint capture sessions from separate evaluation people. Hash, session and frame-level near-duplicate checks found no cross-role collisions. The near-duplicate screen checks a 64-bit perceptual hash and a grayscale thumbnail difference. It cannot rule out all possible transformed duplicates.

| Identification at 3 enrolled people | Observed | Exact binomial 95% interval |
|---|---:|---:|
| Correctly identified enrolled probe | 1 / 1 | 2.50–100% |
| Falsely identified independent unenrolled probe | 0 / 3 | 0–70.76% |

The target of FPIR ≤0.1% at 1,000 distinct enrolled people remains unverified. Separate held-out people form the denominator for these intervals. A single enrollment photo per person and the limited number of lighting and capture environments further limit generalization.

The extended retrieval test used 18 distinct assets, one completed occurrence per asset, 836 decoded frames and 871 face observations. Of those observations, 834 had usable embeddings. Each of two independently captured present probes had three labeled appearances. One belonged to an unenrolled subject. Two additional independent probes belonged to absent subjects and returned zero appearances. A group received credit only if every contributing face had the correct label. Each eligible passage received at most one credit.

| Rank cutoff | Precision@K | Recall@K |
|---|---:|---:|
| 1 | 1.00 | 0.333 |
| 5 | 0.60 | 1.00 |
| 10 | 0.30 | 1.00 |

Precision@K divides correct hits by K, including any unfilled positions. All six returned appearances across the two present probes were relevant.

Both sides of the two-person composite had detections in all 50 labeled frames. The small-face, blur and dim-light segments each had 16 frames, with detections in 16, 9 and 16 respectively. The quality gate admitted 10 small-face observations for search and rejected all detected blurred and dim faces in the stress segment. In 231 observations with a nonfrontal landmark proxy, 4 of 134 enrolled observations reached the correct identity at the conservative cutoff. These correlated frame counts characterize behavior on the clips. They do not increase the independent probe denominator. Eight no-face frames produced zero detections. [Aggregate measurements](pilot-results.json)

## Provenance and access

VidTIMIT is used here for local research under Conrad Sanderson's published research-use conditions. Retain the author's intact notice and required citation. C. Sanderson and B. C. Lovell, “Multi-Region Probabilistic Histograms for Robust and Scalable Identity Inference,” LNCS 5558, pp. 199–208, 2009. The session assignments for speech takes follow page 1 of the dataset documentation. [VidTIMIT license and subject archives](https://www.conradsanderson.id.au/vidtimit/) · [Capture documentation](https://conradsanderson.id.au/vidtimit/zips/vidtimit_documentation.pdf)

Empty-scene imagery is credited to CIRA/NOAA and the NOAA/NESDIS Center for Satellite Applications and Research. [STAR data-use notice](https://star.nesdis.noaa.gov/star/productdisclaimer.php)

PaSC was evaluated as a possible corpus. Notre Dame requires an authorized institutional signature, review and approval before providing download instructions. Its release agreement governs internal research use, redistribution, modification and commercial use. This pilot did not obtain PaSC. [Access process](https://cvrl.nd.edu/projects/data/) · [PaSC release agreement](https://cvrl.nd.edu/media/django-summernote/2018-09-19/766fd40f-fef3-4ae5-a5ab-1b184e9dc4c3.pdf)
