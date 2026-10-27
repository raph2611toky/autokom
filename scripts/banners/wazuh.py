#!/usr/bin/env python3
"""Logo Wazuh (silhouette bleue + W blanc) en Braille Unicode et ANSI 24 bits.

Reproduction vectorielle de l'image de référence fournie, sans image externe,
aucune dépendance Python et aucune connexion réseau.

Utilisation :
    python3 wazuh_banner.py
    python3 wazuh_banner.py --no-title
    python3 wazuh_banner.py --width 40
    python3 wazuh_banner.py --no-color

Terminal UTF-8 recommandé (police prenant en charge le Braille Unicode).
"""

import argparse
import sys

# Bleu échantillonné dans l'image de référence : #1A80B6.
BLUE = (26, 128, 182)
WHITE = (255, 255, 255)
RESET = "\033[0m"

# Silhouette du logo (deux oreilles pointues, ailes latérales, pointe basse).
# Coordonnées extraites de l'image originale, dans un carré 447 x 447.
OUTLINE = (
    (109, 5), (69, 150), (12, 216), (141, 336),
    (187, 446), (248, 446), (293, 337), (424, 217),
    (365, 151), (323, 5), (268, 120), (170, 120),
)

# Chemin blanc formant le W caractéristique (et sa pointe au centre).
WHITE_W = (
    (358, 218), (257, 317), (248, 320), (238, 316),
    (219, 286), (214, 290), (196, 317), (187, 320),
    (178, 317), (78, 222), (79, 220), (104, 220),
    (187, 293), (189, 293), (205, 268), (231, 268),
    (248, 293), (250, 293), (334, 217),
)

# Positions des 8 points d'une cellule Braille, rangés en 4 lignes de 2.
BRAILLE_BITS = (
    (0x01, 0x08),
    (0x02, 0x10),
    (0x04, 0x20),
    (0x40, 0x80),
)


def in_polygon(x, y, vertices):
    """Test de point dans polygone (ray casting)."""
    inside = False
    xa, ya = vertices[-1]
    for xb, yb in vertices:
        if (ya > y) != (yb > y):
            intersection = (xb - xa) * (y - ya) / (yb - ya) + xa
            if x < intersection:
                inside = not inside
        xa, ya = xb, yb
    return inside


def color_at(x, y):
    """0 = vide, 1 = silhouette bleue, 2 = W blanc."""
    if not in_polygon(x, y, OUTLINE):
        return 0
    if in_polygon(x, y, WHITE_W):
        return 2
    return 1


def foreground(rgb):
    return "\033[38;2;{};{};{}m".format(*rgb)


def background(rgb):
    return "\033[48;2;{};{};{}m".format(*rgb)


def banner(width=60, color=True, title=True):
    """Construit l'affichage ANSI/Braille en conservant les proportions du logo."""
    if not 24 <= width <= 120:
        raise ValueError("--width doit être compris entre 24 et 120")

    # Un caractère de terminal est approximativement deux fois plus haut
    # que large. 2x4 points Braille => largeur x (largeur/2) lignes.
    height = round(width / 2)
    blue_fg, blue_bg = foreground(BLUE), background(BLUE)
    white_fg, white_bg = foreground(WHITE), background(WHITE)
    output = []

    for row in range(height):
        pieces = ["  "]
        current_style = ""
        for col in range(width):
            blue = white = 0
            for by in range(4):
                for bx in range(2):
                    x = (col * 2 + bx + 0.5) * 447 / (width * 2)
                    y = (row * 4 + by + 0.5) * 447 / (height * 4)
                    kind = color_at(x, y)
                    if kind == 1:
                        blue |= BRAILLE_BITS[by][bx]
                    elif kind == 2:
                        white |= BRAILLE_BITS[by][bx]

            if not color:
                pieces.append(chr(0x2800 + blue) if blue else " ")
                continue

            occupied = blue | white
            if occupied == 0:
                style, char = "", " "
            elif blue == 255:
                # Zone bleue pleine : vraie couleur de fond, sans trous entre
                # les 8 points Braille.
                style, char = blue_bg, " "
            elif white == 255:
                style, char = white_bg, " "
            elif occupied == 255:
                # Frontière W/bleu : points blancs sur fond bleu plein.
                style, char = blue_bg + white_fg, chr(0x2800 + white)
            elif blue and not white:
                # Bord externe : conserver la transparence du terminal.
                style, char = blue_fg, chr(0x2800 + blue)
            elif white and not blue:
                style, char = white_fg, chr(0x2800 + white)
            else:
                # Les frontières partiellement remplies sont très rares :
                # privilégier la continuité du W blanc sur bleu.
                style, char = blue_bg + white_fg, chr(0x2800 + white)

            if style != current_style:
                pieces.append(RESET + style)
                current_style = style
            pieces.append(char)

        if color:
            pieces.append(RESET)
        output.append("".join(pieces))

    if title:
        label = "W A Z U H".center(width)
        output.extend(("", "  " + (blue_fg + "\033[1m" if color else "") +
                       label + (RESET if color else "")))

    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=60,
                        help="largeur en colonnes, de 24 à 120 (défaut : 60)")
    parser.add_argument("--no-title", action="store_true",
                        help="masquer le texte WAZUH")
    parser.add_argument("--no-color", action="store_true",
                        help="désactiver les couleurs ANSI")
    args = parser.parse_args()
    try:
        print(banner(args.width, not args.no_color, not args.no_title))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
