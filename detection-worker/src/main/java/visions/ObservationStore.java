package visions;

import java.time.Instant;
import java.util.Map;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.S3Exception;

final class ObservationStore {
  interface Proposal {
    byte[] create() throws Exception;
  }

  record Saved(byte[] bytes, String ingestedAt, double ioMs) {}
  private static final String INGESTED_AT = "frame-ingested-at";
  private final S3Client client;
  final String bucket;

  ObservationStore(S3Client client, String bucket) {
    this.client = client;
    this.bucket = bucket;
  }

  Saved resolve(String key, Instant ingestedAt, Proposal proposal) throws Exception {
    long start = System.nanoTime();
    try {
      return read(key, 0);
    } catch (S3Exception missing) {
      if (missing.statusCode() != 404) throw missing;
    }
    double lookupMs = elapsedMs(start);
    byte[] bytes = proposal.create();
    start = System.nanoTime();
    try {
      client.putObject(r -> r.bucket(bucket).key(key).contentType("application/json")
              .metadata(Map.of(INGESTED_AT, ingestedAt.toString()))
              .tagging(System.getenv("DETECTION_RETENTION_TAG")).ifNoneMatch("*"),
          RequestBody.fromBytes(bytes));
      return new Saved(bytes, ingestedAt.toString(), lookupMs + elapsedMs(start));
    } catch (S3Exception conflict) {
      if (conflict.statusCode() != 409 && conflict.statusCode() != 412) throw conflict;
      return read(key, lookupMs + elapsedMs(start));
    }
  }

  private Saved read(String key, double priorMs) {
    long start = System.nanoTime();
    var response = client.getObjectAsBytes(r -> r.bucket(bucket).key(key));
    return new Saved(response.asByteArray(), response.response().metadata().get(INGESTED_AT),
        priorMs + elapsedMs(start));
  }

  private static double elapsedMs(long start) {
    return (System.nanoTime() - start) / 1e6;
  }
}
