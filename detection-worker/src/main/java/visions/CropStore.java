package visions;

import java.awt.Rectangle;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.security.MessageDigest;
import java.util.HexFormat;
import javax.imageio.ImageIO;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;

final class CropStore {
  record Crop(String uri, String sha256, double uploadMs) {}

  private final S3Client client;
  private final String bucket;

  CropStore(S3Client client, String bucket) {
    this.client = client;
    this.bucket = bucket;
  }

  Crop save(String id, BufferedImage image, Rectangle box) throws Exception {
    var buffer = new ByteArrayOutputStream();
    ImageIO.write(image.getSubimage(box.x, box.y, box.width, box.height), "png", buffer);
    byte[] png = buffer.toByteArray();
    String key = "crops/" + id + ".png";
    long start = System.nanoTime();
    client.putObject(r -> r.bucket(bucket).key(key).contentType("image/png").ifNoneMatch("*"),
        RequestBody.fromBytes(png));
    double uploadMs = (System.nanoTime() - start) / 1e6;
    String digest = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(png));
    return new Crop("s3://" + bucket + "/" + key, digest, uploadMs);
  }
}
