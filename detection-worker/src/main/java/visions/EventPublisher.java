package visions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.producer.Producer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.clients.producer.RecordMetadata;

final class EventPublisher {
  private final Producer<String, String> producer;
  private final String topic;
  private final ObjectMapper json;

  EventPublisher(Producer<String, String> producer, String topic, ObjectMapper json) {
    this.producer = producer;
    this.topic = topic;
    this.json = json;
  }

  RecordMetadata publish(JsonNode event) throws Exception {
    String id = event.required("detection_id").asText();
    var record = new ProducerRecord<>(topic, id, json.writeValueAsString(event));
    var ack = producer.send(record).get();
    System.out.printf("detection_id=%s topic=%s partition=%d offset=%d%n",
        id, ack.topic(), ack.partition(), ack.offset());
    return ack;
  }
}
