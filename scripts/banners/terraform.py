#!/usr/bin/env python3
"""Terraform en Braille Unicode et couleurs ANSI. Sans dépendance ni image externe.
Usage : python3 terraform.py [--no-color] [--no-title] [--width N] [--new]
Le logo est défini par sa géométrie vectorielle (4 parallélogrammes) puis
rastérisé à l'exécution en points Braille (2x4 points par caractère).
Logo source : https://github.com/hashicorp/terraform (icône officielle)
"""
import argparse

# Les 4 formes du logo (coordonnées de l'icône officielle, axe Y vers le bas).
GAP = 0.5  # petit espace entre les deux formes empilées (comme sur le logo)
SHAPES = [
    # haut-gauche  "\"
    [(1.000, 0.000), (1.000, 5.050), (5.349, 7.577), (5.349, 2.526)],
    # milieu-haut  "\"
    [(5.826, 2.526), (5.826, 7.577), (10.175, 10.103), (10.175, 5.050)],
    # haut-droite  "/"
    [(10.651, 2.526), (10.651, 7.577), (15.000, 5.050), (15.000, 0.000)],
    # milieu-bas   "\"
    [(5.826, 7.577 + GAP), (5.826, 12.627 + GAP),
     (10.175, 15.153 + GAP), (10.175, 10.103 + GAP)],
]

PURPLE_CLASSIC = (123, 66, 188)   # #7B42BC
PURPLE_NEW = (92, 78, 229)        # #5C4EE5

# Bits Braille : (colonne, ligne) -> masque
DOT_BITS = {(0, 0): 0x01, (0, 1): 0x02, (0, 2): 0x04, (1, 0): 0x08,
            (1, 1): 0x10, (1, 2): 0x20, (0, 3): 0x40, (1, 3): 0x80}


def inside(poly, x, y):
    """Point dans un polygone convexe (signe constant des produits vectoriels)."""
    sign = 0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
        if cross == 0:
            continue
        s = 1 if cross > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


def render(width_chars, rgb):
    xs = [p[0] for s in SHAPES for p in s]
    ys = [p[1] for s in SHAPES for p in s]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    dots_w = width_chars * 2
    scale = dots_w / (maxx - minx)
    dots_h = int((maxy - miny) * scale) + 1
    rows = (dots_h + 3) // 4

    art = []
    for cy in range(rows):
        row = []
        for cx in range(width_chars):
            bits = 0
            for (dx, dy), mask in DOT_BITS.items():
                px = (cx * 2 + dx + 0.5) / scale + minx
                py = (cy * 4 + dy + 0.5) / scale + miny
                if any(inside(s, px, py) for s in SHAPES):
                    bits |= mask
            row.append(chr(0x2800 + bits) if bits else ' ')
        art.append(''.join(row).rstrip())
    return art


def banner(color=True, title=True, width=44, rgb=PURPLE_CLASSIC):
    r, g, b = rgb
    on = f'\033[38;2;{r};{g};{b}m' if color else ''
    off = '\033[0m' if color else ''
    lines = ['  ' + (on + row + off if row else '') for row in render(width, rgb)]
    if title:
        label = 'T E R R A F O R M'.center(width)
        bold = f'\033[1;38;2;{r};{g};{b}m' if color else ''
        lines.extend(['', '  ' + bold + label + off])
    return '\n'.join(lines)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--no-color', action='store_true')
    p.add_argument('--no-title', action='store_true')
    p.add_argument('--width', type=int, default=44,
                   help='largeur en caractères (défaut : 44)')
    p.add_argument('--new', action='store_true',
                   help='couleur HashiCorp récente #5C4EE5 au lieu de #7B42BC')
    a = p.parse_args()
    print(banner(not a.no_color, not a.no_title, a.width,
                 PURPLE_NEW if a.new else PURPLE_CLASSIC))