# Identity-first pilot

Enable `IDENTITY_FIRST_PILOT=1` for **both** services in the operator Compose project after building the UI and rebuilding its Python image. The default is off. On a VM using private image overrides, build the same image from this checkout for recognition and the UI. Open `/?view=identified`. Disabling the flag restores the previous navigation and stops the shadow worker.

The recognizer processes finalized recordings in the background. Run these commands inside the recognition container for a one-time backfill and English engineering review.

```sh
python -m identity.sightings
python -m identity.sightings --review-queue
python -m identity.sightings --withheld
```

Open a candidate's review URL and verify its IRDB reference and observed face against independent labels. Review every member of the proposed sighting before approval. The membership query below accepts the appearance ID as its bound parameter.

```sql
SELECT detection_id, occurrence_id, frame_index, media_pts_s, bbox
FROM sighting_members JOIN indexed USING (detection_id)
WHERE appearance = ? ORDER BY occurrence_id, frame_index;
```

```sh
python -m identity.sightings --approve APPEARANCE --person PERSON_ID \
  --reviewer REVIEWER --representative DETECTION_ID
```

The confirmed page reads reviewed sightings. Approval freezes the sighting. New camera observations can create another candidate. Each representative links back to the frozen gallery version, exact reference crop and original observation. An expired appearance retains its summary. Its evidence panel reports missing media. The player requests only the selected interval plus two seconds on each side and clears its measured box between detections. RTSP times use the recorder clock; replay times use the scheduled occurrence and its playback speed.

## Accuracy evaluation

Use a private JSON array of independently labeled visits. Each visit has a stable ground-truth person ID (including unenrolled subjects), capture session, split and an array of detection IDs belonging to that actual face. Include an empty array for a missed visit. Keep related captures and looped airings in the same split. Include labeled look-alikes, blur, small faces, multiple people, returns and different cameras. Keep the final evaluation separate from threshold selection.

```json
[{"split":"calibration","session":"A-day1","person":"known-1","detections":["d1","d2"]},
 {"split":"evaluation","session":"B-day2","person":"unknown-7","detections":[]}]
```

Run `python -m identity.evaluate_sightings /path/to/visits.json 1 2` with the desired frozen gallery versions. The report compares the published score threshold, calibration threshold, distinct-person margin and one-second agreement on the same labeled visits. It counts unknown false accepts, wrong displayed identities and missed/withheld enrolled visits. Confidence intervals count independent subjects with any error. Cases rejected before embedding contribute withheld observations. This comparison assumes labeled face associations; evaluate grouping separately on crossings and returns. Use the existing VidTIMIT runner for the 8/16/28-person gallery study. Production automation requires held-out camera evidence and an agreed recall floor at the deployment gallery size.

## Five-minute acceptance

1. Select the prepared camera and time window. Count one person with separate visits.
2. Open the labeled two-person sighting. Compare the reference and observed crops and expand the original frame. Check its camera, time and selected box.
3. Click «مشاهده ویدیو». Seek back and play through an unmeasured frame. The overlay must clear.
4. Open the other camera's appearance. Let a polling interval pass and verify that selection stays fixed.
5. Confirm the unknown and near tie in the engineering report. Publish a new gallery version in the test environment and reopen the previous appearance to check its evidence.

Run the isolated API regression with the built operator image from the repository root:

```sh
docker run --rm --network none -e PYTHONPATH=/feed-src:/research-src \
  -v "$PWD/apps/feed/src:/feed-src:ro" -v "$PWD/research/identity/src:/research-src:ro" -v "$PWD/tools/check_identity_first.py:/check.py:ro" \
  visions-operator:identity-pilot python /check.py
```
