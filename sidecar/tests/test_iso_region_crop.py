from pathlib import Path

from PIL import Image

from mercwizard_core.mapforge_engine.iso_renderer import IsoRenderer, sprite_intersects_crop


def test_off_bbox_anchor_with_projected_overlap_is_kept():
    assert sprite_intersects_crop(220, 100, -80, -20, 100, 80, 0,
                                  0, 0, 200, 160)


def test_off_bbox_sprite_without_projected_overlap_is_excluded():
    assert not sprite_intersects_crop(220, 100, 0, 0, 40, 40, 0,
                                      0, 0, 200, 160)


def test_render_keeps_spill_pixels_without_widening_the_crop():
    """An awning may be anchored outside a one-tile crop; its co-tenant may not."""
    rows = cols = 12
    empty = [[] for _ in range(rows * cols)]
    parsed = {
        "rows": rows, "cols": cols, "rooms": [0] * (rows * cols),
        "land": empty.copy(), "objs": empty.copy(), "shadows": empty.copy(),
        "structs": empty.copy(), "roofs": empty.copy(), "onroofs": empty.copy(),
    }
    anchor = 5 * cols + 9  # (9, 5): outside bbox (5, 5), at raw x=80.
    land_only = 5 * cols + 10  # Neighboring land overlaps without a prop.
    parsed["land"][anchor] = [(3, 1)]
    parsed["land"][land_only] = [(4, 1)]
    parsed["structs"][anchor] = [(1, 1)]
    parsed["objs"][anchor] = [(2, 1)]

    class FakeSti:
        def get(self, name):
            # The red awning reaches 100 px left into the crop. The blue
            # co-tenant remains fully outside and must not be drawn.
            if name == "ground":
                return [(Image.new("RGBA", (20, 20), (0, 255, 0, 255)), -100, 0)]
            if name == "ground_only":
                return [(Image.new("RGBA", (10, 10), (255, 255, 0, 255)), -30, 0)]
            return [(Image.new("RGBA", (10, 10),
                               (255, 0, 0, 255) if name == "awning" else (0, 0, 255, 255)),
                     -100 if name == "awning" else 0, 0)]

    renderer = object.__new__(IsoRenderer)
    renderer.dat_path = Path("fixture.dat")
    renderer.parsed = parsed
    renderer.rows = rows
    renderer.cols = cols
    renderer.slot_map = {1: "awning", 2: "co_tenant", 3: "ground", 4: "ground_only"}
    renderer.sti = FakeSti()
    renderer.bg_color = (60, 50, 40, 255)

    canvas = renderer.render(bbox=(5, 5, 5, 5), highlight_room=False)
    assert canvas.size == (160, 260)  # unchanged one-tile camera crop
    colors = set(canvas.getdata())
    assert (0, 255, 0, 255) in colors  # spill anchor's land is admitted first
    assert (255, 255, 0, 255) in colors  # land-only neighbor is independent
    assert (255, 0, 0, 255) in colors
    assert (0, 0, 255, 255) not in colors
