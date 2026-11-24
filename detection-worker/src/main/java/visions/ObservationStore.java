package visions;

import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.S3Exception;

final class ObservationStore {
  interface Proposal {
    byte[] create() throws Exception;
  }

  record Saved(byte[] bytes, double ioMs) {}

  private final S3Client client;
  final String bucket;

  ObservationStore(S3Client client, String bucket) {
    this.client = client;
    this.bucket = bucket;
  }

  Saved resolve(String key, Proposal proposal) throws Exception {
    long start = System.nanoTime();
    try {
      byte[] bytes = read(key);
      return new Saved(bytes, elapsedMs(start));
    } catch (S3Exception missing) {
      if (missing.statusCode() != 404) throw missing;
    }
    double lookupMs = elapsedMs(start);
    byte[] bytes = proposal.create();
    start = System.nanoTime();
    try {
      client.putObject(r -> r.bucket(bucket).key(key).contentType("application/json").ifNoneMatch("*"),
          RequestBody.fromBytes(bytes));
    } catch (S3Exception conflict) {
      if (conflict.statusCode() != 409 && conflict.statusCode() != 412) throw conflict;
      bytes = read(key);
    }
    return new Saved(bytes, lookupMs + elapsedMs(start));
  }

  private byte[] read(String key) {
    return client.getObjectAsBytes(r -> r.bucket(bucket).key(key)).asByteArray();
  }

  private static double elapsedMs(long start) {
    return (System.nanoTime() - start) / 1e6;
  }
}
