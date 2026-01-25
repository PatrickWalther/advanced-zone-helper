# Advanced Zone Helper

A KiCad plugin that creates zones from selected shapes, including ring zones and multi-hole zones between nested outlines.

## Demo

https://github.com/user-attachments/assets/ab84076c-92c4-4066-be21-093f74fc34f7

## Features

- Automatically detects closed loops from selected lines, arcs, bezier elements, and circles
- Creates ring zones between nested outlines (e.g., between two concentric circles)
- Supports multi-hole zones (outer boundary with multiple inner cutouts)
- Configure layer, net, priority, and clearance settings
- Preview detected zones before creation

## Installation

### Option 1: KiCad Plugin Manager (Recommended)

1. Download the latest `advanced-zone-helper-vX.X.X-pcm.zip` from [Releases](../../releases)
2. In KiCad, go to **Plugin and Content Manager**
3. Click **Install from File...** and select the downloaded ZIP

### Option 2: Manual Installation

1. Download the latest `advanced-zone-helper-vX.X.X.zip` from [Releases](../../releases)
2. Extract to your KiCad plugins directory:
   - Windows: `%USERPROFILE%\Documents\KiCad\8.0\scripting\plugins\`
   - Linux: `~/.local/share/kicad/8.0/scripting/plugins/`
   - macOS: `~/Library/Preferences/kicad/8.0/scripting/plugins/`
3. Restart KiCad

## Troubleshooting

- **Plugin not appearing**: Check the plugin is in the correct directory and restart KiCad
- **No shapes found**: Ensure you selected graphic shapes (not footprints/tracks)
- **No closed loops found**: Endpoints must connect within 0.001mm tolerance

## Requirements

- KiCad 7.0 or later

No additional Python packages required - the plugin uses only KiCad's built-in Python environment.

## How the Algorithm Works

1. **[Convert loops to polygons](#1-polygon-representation)** — Discretize curves (arcs, circles, Bézier) into point sequences
2. **[Calculate signed area](#2-signed-area-and-winding-direction-shoelace-formula)** — Use the Shoelace formula to get area and winding direction (CCW = exterior, CW = hole)
3. **[Test point-in-polygon](#3-point-in-polygon-test-ray-casting-algorithm)** — Ray casting to determine if points lie inside a polygon
4. **[Detect containment](#4-polygon-containment-detection)** — Check if all vertices of one polygon lie inside another
5. **[Build containment graph](#5-containment-graph-construction)** — Create a directed graph of which polygons contain which
6. **[Find direct children](#6-direct-containment-and-hole-detection)** — Filter out transitive containment to find immediate holes
7. **[Classify zones](#7-zone-classification)** — Label as Simple (0 holes), Ring (1 hole), or Multi-Hole (2+ holes)
8. **[Calculate area](#8-area-calculation)** — Subtract hole areas from outer boundary area

---

### 1. Polygon Representation

A polygon $P$ is represented as an ordered sequence of $n$ vertices:

```math
P = \{(x_0, y_0), (x_1, y_1), \ldots, (x_{n-1}, y_{n-1})\}
```

For a closed polygon, we define $`P_n = P_0`$ (the last vertex connects back to the first).

**Curve Primitives:** The algorithm handles various geometric primitives which are discretized into point sequences:
- **Line Segments**: Direct vertex-to-vertex connections
- **Arcs**: Approximated using angular subdivision
- **Circles**: Discretized into $n$ equidistant points around the circumference
- **Bézier Curves**: Sampled using De Casteljau's algorithm

---

### 2. Signed Area and Winding Direction (Shoelace Formula)

#### 2.1 The Shoelace Formula

The **signed area** of a simple polygon $P$ with vertices $`(x_0, y_0), (x_1, y_1), \ldots, (x_{n-1}, y_{n-1})`$ is computed using the Shoelace formula (also known as Gauss's area formula). We call it "signed" because the result can be positive or negative depending on whether vertices are ordered counter-clockwise or clockwise—this sign encodes the winding direction.

```math
A_{signed} = \frac{1}{2} \sum_{i=0}^{n-1} (x_i \cdot y_{i+1} - x_{i+1} \cdot y_i)
```

This is the **cross product** of consecutive position vectors, equivalent to the 2×2 determinant:

```math
\begin{vmatrix} x_i & x_{i+1} \\\ y_i & y_{i+1} \end{vmatrix} = x_i \cdot y_{i+1} - x_{i+1} \cdot y_i
```

where indices are taken modulo $n$ (i.e., $`(x_n, y_n) = (x_0, y_0)`$).

#### 2.2 Winding Direction

The **sign** of $`A_{signed}`$ determines the winding direction:

| Sign of $`A_{signed}`$ | Winding Direction |
|---------------------|-------------------|
| $`A_{signed} > 0`$ | **Counter-clockwise (CCW)** - Exterior boundary |
| $`A_{signed} < 0`$ | **Clockwise (CW)** - Hole boundary |

This property is fundamental for distinguishing outer boundaries from holes in complex shapes.

#### 2.3 Implementation

```
function polygon_area(P):
    n = length(P)
    area = 0
    for i = 0 to n-2:
        j = (i + 1) mod n
        area += P[i].x * P[j].y
        area -= P[j].x * P[i].y
    return |area| / 2
```

**Note:** The implementation returns `|area|` (absolute value) for area calculations, but the sign can be preserved for winding direction determination.

#### 2.3 Why Winding Direction Matters for Zone Creation

When creating zones with holes, the outer boundary and holes must have **opposite winding directions**. The algorithm:

1. **Normalizes the outer contour** to positive signed area (CCW in standard coordinates)
2. **Normalizes each hole** to negative signed area (CW — opposite of outer)

KiCAD zones don't support separate hole contours via the SWIG API. Instead, holes are encoded using **zero-width bridges (slits)** connecting the outer boundary to each hole, creating a single continuous polygon:

```
Outer → bridge → Hole (traverse once) → bridge back → continue Outer
```

If winding directions match, the fill engine misinterprets the hole as solid material or produces self-intersections after bridging.

---

### 3. Point-in-Polygon Test (Ray Casting Algorithm)

The **Ray Casting Algorithm** determines whether a point $`Q = (x_q, y_q)`$ lies inside a polygon $P$.

Cast a ray from $Q$ along the positive x-axis and count how many times it crosses the polygon boundary. If the count is **odd**, the point is inside; if **even**, it's outside.

#### 3.1 Edge Crossing Test

For an edge from $`(x_i, y_i)`$ to $`(x_j, y_j)`$, the ray $`y = y_q`$ crosses this edge if:

1. **Vertical span condition**: The edge straddles the ray's y-coordinate: $`(y_i > y_q) \neq (y_j > y_q)`$

2. **Intersection point**: The x-coordinate of intersection is: $`x_{intersect} = x_i + \frac{(y_q - y_i)(x_j - x_i)}{y_j - y_i}`$

3. **Ray direction**: The intersection is to the right of $Q$: $`x_q < x_{intersect}`$

#### 3.2 Implementation

```
function point_in_polygon(Q, P):
    x, y = Q
    n = length(P)
    inside = false
    j = n - 1
    
    for i = 0 to n-1:
        xi, yi = P[i]
        xj, yj = P[j]
        
        if ((yi > y) ≠ (yj > y)) and 
           (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = ¬inside
        
        j = i
    
    return inside
```

---

### 4. Polygon Containment Detection

Polygon $`P_{inner}`$ is **contained within** polygon $`P_{outer}`$ if all vertices of $`P_{inner}`$ lie inside $`P_{outer}`$:

```math
\text{contains}(P_{outer}, P_{inner}) = \bigwedge_{i=0}^{m-1} \text{point\_in\_polygon}(P_{inner}[i], P_{outer})
```

where $m$ is the number of vertices in $`P_{inner}`$.

#### 4.1 Area Ratio Filtering

To prevent false positives from nearly-identical polygons (due to numerical precision), an **area ratio test** is applied:

```math
r = \frac{A(P_{inner})}{A(P_{outer})}
```

If $r > 0.99$, the polygons are considered identical and containment is rejected:

```math
\text{valid\_containment} = \text{all\_points\_inside} \land (r \leq 0.99)
```

---

### 5. Containment Graph Construction

Given $n$ polygons, we build a **containment graph** where an edge from $i$ to $j$ means polygon $i$ contains polygon $j$. This is computed by testing all pairs:

```
function build_containment_graph(polygons):
    n = length(polygons)
    containment = [[] for i in range(n)]
    
    for i = 0 to n-1:
        for j = 0 to n-1:
            if i ≠ j and polygon_contains_polygon(polygons[i], polygons[j]):
                containment[i].append(j)
    
    return containment
```

---

### 6. Direct Containment and Hole Detection

A polygon $`P_j`$ is a **direct child** (immediate hole) of $`P_i`$ if $`P_i`$ contains $`P_j`$ with no intermediate polygon between them.

Since containment is transitive (if $A$ contains $B$ and $B$ contains $C$, then $A$ contains $C$), we filter out indirect relationships:

```
function find_direct_children(containment):
    n = length(containment)
    direct_children = [[] for i in range(n)]
    
    for i = 0 to n-1:
        for j in containment[i]:
            if is_direct_containment(i, j, containment):
                direct_children[i].append(j)
    
    return direct_children

function is_direct_containment(outer, inner, containment):
    for k in containment[outer]:
        if k ≠ inner and inner in containment[k]:
            return false  # Found intermediate polygon
    return true
```

---

### 7. Zone Classification

Based on the number of direct children (holes), polygons are classified:

| Direct Children | Zone Type | Description |
|----------------|-----------|-------------|
| 0 | **Simple Zone** | Solid region with no holes |
| 1 | **Ring Zone** | Annular region (1 outer, 1 inner boundary) |
| ≥2 | **Multi-Hole Zone** | Region with multiple holes |

---

### 8. Area Calculation

**Ring Zone:** $`A_{ring} = A(P_{outer}) - A(P_{inner})`$

**Multi-Hole Zone:** $`A_{multi} = A(P_{outer}) - \sum_{i=1}^{k} A(H_i)`$ where $k$ is the number of holes.

## License

MIT License
