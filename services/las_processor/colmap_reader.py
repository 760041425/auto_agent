from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class ColmapImage:
    image_id: int
    qw: float
    qx: float
    qy: float
    qz: float
    tx: float
    ty: float
    tz: float
    camera_id: int
    name: str
    points2d: list[tuple[float, float, int]] = field(default_factory=list)


@dataclass
class ColmapPoint3D:
    point_id: int
    x: float
    y: float
    z: float
    r: int
    g: int
    b: int
    track: list[tuple[int, int]] = field(default_factory=list)


def read_images_txt(path: str) -> list[ColmapImage]:
    images = []
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    i = 0
    while i + 1 < len(lines):
        header_parts = lines[i].split()
        if len(header_parts) < 10:
            i += 2
            continue
        try:
            img = ColmapImage(
                image_id=int(header_parts[0]),
                qw=float(header_parts[1]), qx=float(header_parts[2]),
                qy=float(header_parts[3]), qz=float(header_parts[4]),
                tx=float(header_parts[5]), ty=float(header_parts[6]),
                tz=float(header_parts[7]),
                camera_id=int(header_parts[8]),
                name=" ".join(header_parts[9:]),
            )
        except (ValueError, IndexError):
            i += 2
            continue

        pts = lines[i + 1].split()
        for j in range(0, len(pts) - 2, 3):
            try:
                x = float(pts[j])
                y = float(pts[j + 1])
                pid = int(pts[j + 2])
                img.points2d.append((x, y, pid))
            except (ValueError, IndexError):
                pass
        images.append(img)
        i += 2
    return images


def read_points3d_txt(path: str) -> dict[int, ColmapPoint3D]:
    points = {}
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    for line in lines:
        parts = line.split()
        if len(parts) < 8:
            continue
        pid = int(parts[0])
        p = ColmapPoint3D(
            point_id=pid,
            x=float(parts[1]), y=float(parts[2]), z=float(parts[3]),
            r=int(parts[4]), g=int(parts[5]), b=int(parts[6]),
        )
        tracks = parts[8:]
        for j in range(0, len(tracks), 2):
            if j + 1 < len(tracks):
                p.track.append((int(tracks[j]), int(tracks[j + 1])))
        points[pid] = p
    return points
