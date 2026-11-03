#!/usr/bin/env python3
"""Jenkins en caractères Braille Unicode : sans image, sans dépendance.
Usage : python3 jenkins_contours.py [--no-color] [--no-title]
"""
import argparse
ART = ['                          ⢀⣀⣠⣤⣤⣤⣤⣤⣤⣄⣀⣀', '                     ⢀⣠⣴⣶⣿⣿⣿⠿⠟⠛⠛⠛⠛⠛⠛⠿⢿⣿⣷⣦⣄⡀', '                  ⣀⣤⡶⠟⠛⣩⡽⠟⠉            ⠉⠙⠿⣿⣦⡀', '               ⢀⣴⡿⠛⢁⣠⣶⠟⠋                  ⠈⠻⣿⣦⡀', '              ⣰⡿⠋⢀⣴⣿⡿⠁                      ⠈⢻⣿⣆', '            ⢀⣴⣿⣷⡿⠿⠛⣿⡀      ⢀⣤⣤⣤⡀          ⢀⡶⠿⣿⣿⣿⣆', '           ⣰⣿⡿⠋⣠⣴⣶⣿⣿⡇    ⢀⣴⠟⠋⠉⠉⠁              ⠈⠻⣿⡀', '          ⢀⣿⡟⢠⣾⣿⣿⣿⣿⣿⠃    ⠸⠋                     ⢹⡇', '          ⣸⣿⡇⣾⣿⣿⣿⣿⣿⡿             ⡆     ⢀      ⣦ ⠸⣿', '        ⣠⣾⣿⣿⡇⣿⣿⣿⣿⣿⣿⡇             ⠹⣆    ⠸⣧     ⢘⣧ ⣿⣦⡀', '      ⣠⣾⣿⣿⣿⣿⣿⠿⠟⠿⢿⣿⣿⣧          ⠰⣶⣦⣴⣿⠇    ⠹⣷⡀ ⠻⠿⠟⠋ ⣿⣿⣿⣄', '    ⢀⣴⣿⣿⣿⣿⣿⡟⠁⣤⣶⣤⡀⢻⣿⣿⡄           ⠉⠉       ⠘⢿⣦     ⣿⣿⣿⣿⣷⡀', '   ⢀⣾⣿⣿⣿⣿⣿⣿⡁ ⢠⡏⠉⢷⠈⠉⠉                       ⠙⣷⣄  ⢰⣿⣿⣿⣿⣿⣿⡄', '  ⢠⣿⣿⣿⣿⣿⣿⣿⣿⣇ ⠈⠇                             ⣼⡏  ⣸⣿⣿⣿⣿⣿⣿⣿⡄', ' ⢀⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣦⡀   ⣀                  ⠸⣧⣀⣀⣤⣴⠾⠛⠉ ⡀⢀⣿⣿⣿⣿⣿⣿⣿⣿⣿⡄', ' ⣼⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣶⣶⣾⠟               ⢦⣤⣄⣀⣉⣉⣉⡁⢀⣠⣤⣶⠟⠁⣼⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷', '⢰⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡀     ⢀       ⠐⢦⡀⠈⠙⠛⠻⠿⠿⠇⠸⠛⠛⠁ ⣰⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇', '⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣇      ⢳⡀       ⠻⣶⣤⣤⣤⣤⣤⣤⡄   ⣴⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿', '⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣆      ⠹⣦⡀       ⠉⠉⠉⠉⠉⠁  ⢀⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡆', '⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣆      ⠈⠻⣦⣀          ⢀⣠⣴⣿⠟⠻⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇', '⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠿⠛⠋⠻⣿⣷⣦⣄⣀⣀⡀⢀⣠⣤⣽⣿⣶⣤⣤⣄⣀⣤⣤⣴⣾⣿⣿⣿⣿⣷⣄⠈⠻⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇', '⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠟⠋⠁ ⢀⣀⣤⣾⣿⣿⣬⣉⠉⠉⣻⡏⠉⠉⠙⠻⢿⣿⣯⣉⣉⣉⣥⡾⠛⠉  ⢻⣿⣷⣦⡈⠻⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇', '⣿⣿⣿⣿⣿⣿⣿⣿⣿⠿⠋ ⣀⣠⣴⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⣿      ⠈⢙⣻⠋⠙⣿⡤    ⠸⣿⣿⣿⣿⣷⣿⣿⣿⣿⣿⣿⣿⣿⣿⠃', '⢹⣿⣿⣿⣿⣿⣿⣿⡅ ⢰⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡄      ⠈⢹⣧⣤⣿⣆⣀⣀  ⢰⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿', '⠘⣿⣿⣿⣿⣿⣿⣿⣿⡀⠘⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣧     ⣠⣾⣯⠉ ⢀⠉⠛⣿⠛⠛⠻⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠇', ' ⢹⣿⣿⣿⣿⣿⣿⣿⣧ ⢻⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣧⣤⣤⣶⠟⠉ ⠻⣷⡀⠙⠁ ⡏  ⢠⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡟', '  ⢻⣿⣿⣿⣿⣿⣿⣿⡄⢸⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡏⣿⣿⣿⣿⣿⣿⣿⡿⠁    ⠹⣷⡀⠶⢸⡇ ⣰⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠁', '   ⢻⣿⣿⣿⣿⣿⣿⣷ ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇⣿⣿⣿⣿⣿⣿⣿⣿⣶⣄⣀   ⠹⣧ ⢸⠃⣴⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠁', '    ⠻⣿⣿⣿⣿⣿⣿⡄⢹⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣶⣤⣄⣻⣧⣿⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠿⠿⠿⣷⣆', '     ⠙⢿⣿⣿⣿⣿⡇⢸⣿⣿⣿⣿⣿⣿⣿⣿⣿⣇⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠟⠋⠉⢀   ⠈⣿⣆', '       ⠻⣿⣿⣿⣿⠸⣿⣿⣿⣿⣿⠿⠿⠿⢿⡟⠛⠋⠉⠉⠉⠁        ⠈⠉⣿⡏⠉⢻⣿⡄ ⢻⡇ ⠴⠚⠉    ⢸⣿⡄', '        ⠈⠻⣿⣿ ⣿⣿⣿⣿⣿⣿⣿⣿⣾⡇                ⣿⣧⣶⣾⣿⡇ ⠈⣿  ⠉⠉⠉    ⣿⣧', '          ⢻⣿ ⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷                ⣿⣿⣿⣿⣿⣷  ⣿  ⠉⠉⠉⠉   ⣿⡇', '          ⢸⣿⣇⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡀               ⢻⣿⣿⣿⣿⣿⣀⣀⣿⣦⣤⣤⣤⠤⠤ ⢀⣴⡿⠁', '          ⠸⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇               ⢸⣿⣿⣿⣿⣿⡟⠋⠉⠉⠉⠻⠷⠶⠶⠿⠿⠛⠁', '            ⠈⠙⠻⢿⣿⣿⣿⣿⣿⣿⣿⣷               ⢸⡟⠛⢻⣿⠉', '                ⠈⠉⠛⠻⠿⠿⠿⣿⡄              ⢸⡇ ⠘⣿⡀', '                       ⢿⣧              ⠸⡇  ⣿⣇', '                       ⠘⣿⡄              ⣧⣠⣤⣾⡟', '                        ⢹⣷              ⣿⡟⠋⠉', '                         ⢿⣷⣶⣦⣤⣤⣄⣀⣀⣀⣀⣤⣤⣤⣶⣿⠇', '                          ⠉⠉⠙⠛⠛⠛⠛⠛⠛⠛⠛⠛⠋⠉⠁']
COLORS = ['wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww', 'wwwwwwwwwwwwwwwwwwwwwwwwwsssssssssssssssswwwwwwwwwwwwwwwwwww', 'wwwwwwwwwwwwwwwwwwwwwwwssssssssssssssssssssswwwwwwwwwwwwwwww', 'wwwwwwwwwwwwwwwwwwwwsssssssssssssssssssssssssswwwwwwwwwwwwww', 'wwwwwwwwwwwwwwwwwwwssssssssssssssssssssssssssswwwwwwwwwwwwww', 'wwwwwwwwwwwwwwwwwwwssssssssssssssssssssssssssssswwwwwwwwwwww', 'wwwwwwwwwwwwwwwwwwwsssssssssssssssssssssssssssssswwwwwwwwwww', 'wwwwwwwwwwwwwwwwwwwssssssssssssssssssssssssssssssswwwwwwwwww', 'wwwwwwwwwwwwwwwwwwsssssssssssssssssssssssssssssssswwwwwwwwww', 'wwwwwwwwjrrrjwwwwwsssssssssssssssssssssssssssssssswwwwwwwwww', 'wwwwwwrrrrrrssssssssssssssssssssssssssssssssssssssrrjwwwwwww', 'wwwwwrrrrrrssssssssssssssssssssssssssssssssssssssrrrrrwwwwww', 'wwwwrrrrrrrssssssssssssssssssssssssssssssssssssssrrrrrrwwwww', 'wwjrrrrrrrrssssssssssssssssssssssssssssssssssssssrrrrrrrjwww', 'wjrrrrrrrrrrssssssssssssssssssssssssssssssssssssrrrrrrrrrwww', 'wjrrrrrrrrrrrrssssssssssssssssssssssssssssssssssrrrrrrrrrrww', 'jrrrrrrrrrrrrrrrsssssssssssssssssssssssssssssssrrrrrrrrrrrww', 'jrrrrrrrrrrrrrrrrsssssssssssssssssssssssssssssrrrrrrrrrrrrrw', 'rrrrrrrrrrrrrrrrrrsssssssssssssssssssssssssssrrrrrrrrrrrrrrj', 'rrrrrrrrrrrrrrrrrjjssssssssssssssssssssssssjjjjrrrrrrrrrrrrj', 'rrrrrrrrrrrrrrjjjjjjsssssssssssssssssssssrrjjjjjjrrrrrrrrrrj', 'rrrrrrrrrrrjjjjjjjjjjjjjssrrrrrrrrrsssrrrrrrrjjjjjjrrrrrrrrj', 'rrrrrrrrjjjjjjjjjjjjjjjjjjrrrrrrrrrrrrrrrrrrrjjjjjjrrrrrrrrw', 'jrrrrrrjjjjjjjjjjjjjjjjjjjjrrrrrrrrrrrrrrrrrrjjjjjjrrrrrrrjw', 'jrrrrrrrjjjjjjjjjjjjjjjjjjjjrrrrrrrrrwrrrjjjjjjjjjrrrrrrrrjw', 'wwrrrrrrjjjjjjjjjjjjjjjjjjjjjrrrjjjjjjwwjjjjjjjjjrrrrrrrrrww', 'wwrrrrrrrjjjjjjjjjjjjjjjjjjjjjjjjjjjjjwwjjjjjjjjjrrrrrrrrwww', 'wwwrrrrrrrjjjjjjjjjjjjjjjjjjjjjjjjjjjjjwjjjjjjjjrrrrrrrrwwww', 'wwwwrrrrrrjjjjjjjjjjjjjjjjjjjjjjjjjjjjjwjjjjjjjjrrrrrrrwwwww', 'wwwwwwrrrrjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjwwwwwwwww', 'wwwwwwjjrrjjjjjjjjjjjjjjjjjjwwwwwwwwwwwjjjjjwwwwwwwwwwwwwwww', 'wwwwwwwwwwjjjjjjjjjjjjjwwwwwwwwwwwwwwwwjjjjjwwwwwwwwwwwwwwww', 'wwwwwwwwwwjjjjjjjjjjjjjjwwwwwwwwwwwwwwwjjjjjjwwwwwwwwwwwwwww', 'wwwwwwwwwwwjjjjjjjjjjjjjwwwwwwwwwwwwwwwjjjjjjwwwwwwwwwwwwwww', 'wwwwwwwwwwwjjjjjjjjjjjjjwwwwwwwwwwwwwwwjjjjjjwwwwwwwwwwwwwww', 'wwwwwwwwwwwwwwjjjjjjjjjjwwwwwwwwwwwwwwwjjjjjwwwwwwwwwwwwwwww', 'wwwwwwwwwwwwwwwwwwjjjjjjwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww', 'wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww', 'wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww', 'jjjjjjwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww', 'jjjjjjjwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww', 'jjjjjjjjwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww']
PALETTE = {'w': (235, 235, 235), 'r': (242, 72, 72), 's': (245, 205, 155), 'j': (90, 185, 210)}

def banner(color=True, title=True):
    lines = []
    for row, shades in zip(ART, COLORS):
        line = '  '
        last = None
        for ch, shade in zip(row, shades):
            if color and shade != last and ch != ' ':
                r, g, b = PALETTE[shade]
                line += f'\033[38;2;{r};{g};{b}m'
                last = shade
            line += ch
        lines.append(line + ('\033[0m' if color else ''))
    if title:
        label = 'J E N K I N S'.center(60)
        lines.extend(['', '  ' + ('\033[1;91m' if color else '') + label + ('\033[0m' if color else '')])
    return '\n'.join(lines)

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--no-color', action='store_true')
    p.add_argument('--no-title', action='store_true')
    a = p.parse_args()
    print(banner(not a.no_color, not a.no_title))
