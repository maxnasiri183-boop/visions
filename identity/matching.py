"""Resident exact cosine index for one immutable gallery version."""

import numpy as np


class ExactGallery:
    def __init__(self):
        self.version = None
        self.ids = []
        self.matrix = np.empty((0, 128), dtype="<f4")

    def load(self, db, version):
        if self.version == version:
            return
        count = db.execute(
            "SELECT count(*) FROM membership WHERE version=?", (version,)
        ).fetchone()[0]
        matrix = np.empty((count, 128), dtype="<f4")
        ids = []
        rows = db.execute(
            """SELECT r.reference_id,r.embedding FROM membership m JOIN refs r USING(reference_id)
            WHERE m.version=? ORDER BY m.reference_id""",
            (version,),
        )
        for i, row in enumerate(rows):
            matrix[i] = np.frombuffer(row["embedding"], dtype="<f4")
            ids.append(row["reference_id"])
        self.matrix, self.ids, self.version = matrix, ids, version

    def classify(self, vector, threshold):
        if not self.ids:
            return "unknown", None, None, "No eligible gallery references"
        query = np.frombuffer(vector, dtype="<f4")
        scores = self.matrix @ query
        best = int(np.argmax(scores))
        # Match the original scalar score at the decision boundary.
        score = float(np.clip(self.matrix[best] @ query, -1, 1))
        if score >= threshold:
            return "matched", self.ids[best], score, None
        return "unknown", self.ids[best], score, "Similarity below gallery threshold"
