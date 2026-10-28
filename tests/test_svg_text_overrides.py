from core.utils.svg_text import apply_svg_text_overrides, svg_text_entries


def test_svg_text_entries_and_overrides_preserve_svg_text_attributes() -> None:
    source = b'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80">
      <text x="10" y="20" style="font-size:10px">Control</text>
      <text x="10" y="40" transform="rotate(-90)"><tspan>Area (um2)</tspan></text>
    </svg>'''

    entries = svg_text_entries(source)

    assert [(entry.index, entry.text) for entry in entries] == [(0, "Control"), (1, "Area (um2)")]
    rendered = apply_svg_text_overrides(source, {"1": "Fiber area (um2)"})
    updated_entries = svg_text_entries(rendered)

    assert [(entry.index, entry.text) for entry in updated_entries] == [(0, "Control"), (1, "Fiber area (um2)")]
    assert b'transform="rotate(-90)"' in rendered
    assert updated_entries[1].rotation_deg == -90.0


def test_svg_text_entries_preserve_parent_group_rotation() -> None:
    source = b'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80">
      <g transform="translate(12, 40) rotate(-90)">
        <text x="0" y="0">Frequency</text>
      </g>
    </svg>'''

    entries = svg_text_entries(source)

    assert [(entry.text, entry.x, entry.y, entry.rotation_deg) for entry in entries] == [("Frequency", 0.0, 0.0, -90.0)]


def test_svg_text_overrides_support_relative_position_and_backward_compatible_strings() -> None:
    source = b'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80">
      <text x="10" y="20">Control</text>
      <text x="15" y="40">Area (um2)</text>
    </svg>'''

    rendered = apply_svg_text_overrides(
        source,
        {
            "0": "Treatment",
            "1": {"text": "Fiber area (um2)", "dx": 9.5, "dy": 8.0},
        },
    )
    updated_entries = svg_text_entries(rendered)

    assert [(entry.index, entry.text) for entry in updated_entries] == [(0, "Treatment"), (1, "Fiber area (um2)")]
    assert updated_entries[0].x == 10.0
    assert updated_entries[0].y == 20.0
    assert updated_entries[1].x == 24.5
    assert updated_entries[1].y == 48.0
    assert b'x="24.5"' in rendered
    assert b'y="48"' in rendered
