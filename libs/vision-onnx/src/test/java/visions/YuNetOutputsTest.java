package visions;

import java.awt.Rectangle;
import java.nio.FloatBuffer;
import java.util.ArrayList;
import java.util.List;
import org.junit.Test;
import static org.junit.Assert.*;

public class YuNetOutputsTest {
  @Test
  public void decodesScoreAndClipsBoxesInSourceCoordinates() {
    int cells = 20 * 20;
    var cls = FloatBuffer.allocate(cells);
    var obj = FloatBuffer.allocate(cells);
    cls.put(0, 2f);  // clamp to 1 before combining scores
    obj.put(0, .81f);
    cls.put(1, Float.NaN);
    obj.put(1, 1f);
    var out = new ArrayList<YuNet.Face>();
    YuNetOutputs.decodeStride(out, 32, cls, obj, FloatBuffer.allocate(4 * cells),
        FloatBuffer.allocate(10 * cells), 512, 256, 1.25, 1.25);
    assertEquals(1, out.size());
    assertEquals(new Rectangle(0, 0, 13, 13), out.getFirst().box());
    assertEquals(.9, out.getFirst().score(), 1e-6);
    assertEquals(5, out.getFirst().points().size());
  }

  @Test
  public void suppressionKeepsHighestScoreAndNonOverlappingFaces() {
    var first = new YuNet.Face(new Rectangle(0, 0, 10, 10), List.of(), .91);
    var overlap = new YuNet.Face(new Rectangle(1, 1, 10, 10), List.of(), .90);
    var other = new YuNet.Face(new Rectangle(20, 0, 10, 10), List.of(), .95);
    assertEquals(List.of(other, first), YuNetOutputs.suppress(new ArrayList<>(List.of(overlap, first, other))));
  }
}
