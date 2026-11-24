package visions;

import java.nio.charset.StandardCharsets;
import org.junit.Test;
import static org.junit.Assert.*;
import software.amazon.awssdk.core.ResponseBytes;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.*;

public class ObservationStoreTest {
  @Test
  public void conditionalConflictUsesWinnersBytes() throws Exception {
    byte[] winner = "[{\"detection_id\":\"peer\"}]".getBytes(StandardCharsets.UTF_8);
    var client = new S3Client() {
      int reads;

      @Override public String serviceName() { return "s3"; }
      @Override public void close() {}

      @Override public ResponseBytes<GetObjectResponse> getObjectAsBytes(GetObjectRequest request) {
        if (reads++ == 0) throw S3Exception.builder().statusCode(404).build();
        return ResponseBytes.fromByteArray(GetObjectResponse.builder().build(), winner);
      }

      @Override public PutObjectResponse putObject(PutObjectRequest request, RequestBody body) {
        assertEquals("*", request.ifNoneMatch());
        throw S3Exception.builder().statusCode(412).build();
      }
    };
    var store = new ObservationStore(client, "test");
    assertArrayEquals(winner, store.resolve("key", java.time.Instant.EPOCH, () -> new byte[0]).bytes());
    assertArrayEquals(winner, store.resolve("key", java.time.Instant.EPOCH, () -> { throw new AssertionError("unexpected inference"); }).bytes());
  }
}
