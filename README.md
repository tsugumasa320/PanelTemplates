# PanelTemplates - KiCad Templates for creating EURORACK panels
##### Do you want to create PCB Eurorack panels in KiCad? This is the hassle free way to set up the basics.

How to use it?

1. Download this repository and put it *preferrably* in your KiCad templates folder. Make sure you unzip the .zip

###### KiCad templates can be found by clicking Preferences -> Configure paths, find KICAD_USER_TEMPLATE_DIR

2. Open KiCad (make sure you are on 6.0.5 or newer)
3. Click on File -> New Project from Template
4. Click on user templates and select the folder of PanelTemplates
![Project Template Selector Picture](ProjectTemplateSelector.png)
###### Now all of the templates are showing up, select the size you want
5. Click OK and select where to create your KiCad panel project

##### This project contains blank templates for 3U panels in these widths:
- 02HP -  9.6  mm
- 04HP - 20    mm
- 06HP - 30    mm
- 08HP - 40.3  mm
- 10HP - 50.5  mm
- 12HP - 60.6  mm
- 14HP - 70.8  mm
- 16HP - 80.9  mm
- 18HP - 91.3  mm
- 20HP - 101.3 mm
- 21HP - 106.3 mm
- 22HP - 111.4 mm
- 28HP - 141.9 mm
- 42HP - 213   mm

3U height is 128.5 mm according to [Doepfer A-100 Construction Details](https://doepfer.de/a100_man/a100m_e.htm).

##### Also blank templates for Intellijel 1U panels in the same widths:

1U Intellijel height is **39.65 mm**. Mounting holes use the same horizontal positions as the matching 3U templates, with vertical positions 3.0 mm from the top and bottom edges (Y = 3.0 and 36.65).

Folder naming: `1U_Intellijel_XXHP_Blank`

##### Converter panels (Intellijel)

These mount with M3 screws and nuts (no tapped holes on the adapter). Module mounts use **rounded Edge.Cuts slots** (capsule / stadium shape) so HP and position are flexible.

**`3U_to_1U_Intellijel_26HP`** — put rotated 3U modules into an Intellijel 1U row

- Outer size: 131.7 × 39.65 mm (26HP × Intellijel 1U)
- Rail slots: horizontal 8.0 × 3.2 mm at X = 7.5 / 124.34, Y = 3.0 / 36.65
- Center window: 112 × 22 mm (fits up to about 4HP of 3U)
- Module slots: vertical 3.2 × 18.0 mm at X = 4.6 / 127.1 (122.5 mm apart) for Y adjustment across 2–4HP

**`1U_to_3U_Intellijel_16HP`** — put Intellijel 1U modules into a 3U row (3 tiers)

- Outer size: 80.9 × 128.5 mm (16HP × 3U)
- Rail holes: plated ovals same as `3U_16HP_Blank` (X = 7.5 / 73.54, Y = 3.0 / 125.5)
- Three 1U rows; each row has top/bottom horizontal slots (3.2 mm tall, X = 10.0–70.9) so modules can sit anywhere along ~14HP

Dimensions follow [Doepfer A-100](https://doepfer.de/a100_man/a100m_e.htm) and [Intellijel 1U Technical Specifications](https://intellijel.com/support/1u-technical-specifications/). Commercial references for layout size: 26HP 3U→1U and 16HP 3-row 1U→3U adapters.

##### Relief-textured panels (1U Intellijel 8HP)

`relief/` holds 36 blanks carrying a full-bleed repeating geometric pattern.
Nothing is milled through the board: the texture comes from the copper and
soldermask layers a plain PCB process already gives you, so each panel is a
normal 1.6 mm blank with all four mounting pads intact and costs exactly what
an untextured blank costs. Copy any folder next to the other templates for
KiCad to list it.

Two ways to realise the same pattern, selected when you generate:

| Style | How it is built | Result |
|-------|-----------------|--------|
| `emboss` (default) | Pattern on `F.Cu`, soldermask left closed over the whole panel | One colour throughout; the pattern stands 35 µm proud and reads as relief that catches the light. Order **2 oz outer copper** to double the step to 70 µm. |
| `expose` | Solid copper pour with the pattern opened in `F.Mask` | Pattern comes out as bare plated metal against matte mask: strong gloss and colour contrast, almost no step. |

The stackup is written into every panel as black soldermask over ENIG, which is
both what the fab needs and what makes KiCad's 3D view look right.

Regenerate from the repo root:

```bash
python3 _gen_relief.py                  # emboss, into relief/
python3 _gen_relief.py --style expose
```

The generator clips every pattern to a 0.3 mm copper-free border, then checks
each panel for copper features and gaps below 0.15 mm so nothing falls under a
standard 5 mil process.

| Family | Patterns |
|--------|----------|
| Tilings | `Honeycomb` `Triangles` `Harlequin` `Basketweave` `OctagonSquare` `Herringbone` `Parquet` `Rhombille` `Pinwheel` `CrossPlus` `TriHexSolid` `Waffle` |
| Lattices | `TriHex` `Kagome` `Asanoha` `FlowerOfLife` `HexRing` `RingLattice` `NestedSquares` `StepTerrace` `Coffer` `RivetGrid` |
| Bands and curves | `Chevron` `Ribs` `DiagonalRibs` `Interlace` `Truchet` `ScallopFan` `FishScale` `GreekKey` |
| Studs and dots | `DiamondPlate` `Dimples` `StarGrid` `DotGrid` `DotStagger` `DotDuo` |

`python3 _preview_patterns.py --mode emboss` renders the whole set as a PNG
contact sheet if you want to browse before generating.

Blank templates include edge cuts with plated oval holes; converters add window / long-slot cutouts for flexible mounting.
![This is an image](8HP.png)

###### Shoutout to [@mzourack](https://github.com/mzourack) for his oval holes lol.

##### Discussion, feedback and feature requests are welcome. Just hop on into the discussion section or create an issue.
