package visions;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import org.apache.kafka.clients.producer.MockProducer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.junit.Test;
import static org.junit.Assert.*;

public class EventPublisherTest {
  @Test
  public void completionRequiresKafkaAcknowledgement() throws Exception {
    var json = new ObjectMapper();
    try (var producer = new MockProducer<String, String>(false, new StringSerializer(), new StringSerializer())) {
      var publisher = new EventPublisher(producer, "face.test", json);
      var result = CompletableFuture.supplyAsync(() -> {
        try {
          return publisher.publish(json.createObjectNode().put("detection_id", "key"));
        } catch (Exception e) {
          throw new RuntimeException(e);
        }
      });
      long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(3);
      while (producer.history().isEmpty() && System.nanoTime() < deadline) Thread.sleep(5);
      assertFalse(producer.history().isEmpty());
      assertFalse(result.isDone());
      assertEquals("key", producer.history().getFirst().key());
      assertEquals("{\"detection_id\":\"key\"}", producer.history().getFirst().value());
      producer.errorNext(new RuntimeException("broker unavailable"));
      assertThrows(ExecutionException.class, () -> result.get(3, TimeUnit.SECONDS));
    }
  }
}
