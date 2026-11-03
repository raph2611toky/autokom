
#!/usr/bin/env bash
# AUTOKOM — bannière SSH, cadre simple et coins arrondis.
# Aucun effet escalier, aucune ligne de texte superflue.

# Compter correctement les caractères UTF-8 (et non les octets).
if locale -a 2>/dev/null | grep -Eiq '^C\.utf-?8$'; then
    export LC_CTYPE=C.UTF-8
fi

repeat() {
    local char=$1 n=$2 i
    for ((i = 0; i < n; i++)); do printf '%s' "$char"; done
}

banner() {
    local cols=80 inner=69 border reset green yellow
    local cyan light row middle i
    local -a logo=(
        ' █████╗ ██╗   ██╗████████╗ ██████╗ ██╗  ██╗ ██████╗ ███╗   ███╗'
        '██╔══██╗██║   ██║╚══██╔══╝██╔═══██╗██║ ██╔╝██╔═══██╗████╗ ████║'
        '███████║██║   ██║   ██║   ██║   ██║█████╔╝ ██║   ██║██╔████╔██║'
        '██╔══██║██║   ██║   ██║   ██║   ██║██╔═██╗ ██║   ██║██║╚██╔╝██║'
        '██║  ██║╚██████╔╝   ██║   ╚██████╔╝██║  ██╗╚██████╔╝██║ ╚═╝ ██║'
        '╚═╝  ╚═╝ ╚═════╝    ╚═╝    ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚═╝     ╚═╝'
    )

    # Largeur effective de la fenêtre SSH.
    if [[ -t 1 ]] && command -v tput >/dev/null 2>&1; then
        cols=$(tput cols 2>/dev/null) || cols=80
    elif [[ ${COLUMNS:-} =~ ^[0-9]+$ ]]; then
        cols=$COLUMNS
    fi
    [[ ${AUTOKOM_COLUMNS:-} =~ ^[0-9]+$ ]] && cols=$AUTOKOM_COLUMNS

    reset='' border='' cyan='' light='' green='' yellow=''
    if [[ -t 1 && ${TERM:-dumb} != dumb && ! ${NO_COLOR+x} ]]; then
        reset=$'\033[0m'
        border=$'\033[1;34m'   # Cadre bleu
        cyan=$'\033[1;96m'     # Haut du logo : cyan clair
        light=$'\033[1;36m'    # Bas du logo : cyan
        green=$'\033[1;32m'    # Signature verte
        yellow=$'\033[1;33m'   # Engrenages jaunes
    fi

    printf '\n'
    if (( cols < inner + 3 )); then
        printf '%s⚙  AUTOKOM · Automation by IKOM  ⚙%s\n\n' "$green" "$reset"
        return 0
    fi

    # Bordure supérieure : rectangle doux, sans marches.
    printf ' %s╭%s╮%s\n' "$border" "$(repeat '─' "$inner")" "$reset"

    # Impression d'une ligne, avec calcul avant les codes couleur.
    # Paramètre 1 : texte brut ; 2 : couleur ; 3 : rendu coloré facultatif.
    banner_line() {
        local text=$1 tone=${2:-} rendered=${3:-}
        local padding=$((inner - ${#text}))
        local l=$((padding / 2))
        local r=$((padding - l))
        if ((padding < 0)); then
            return 1
        fi
        [[ -n $rendered ]] || rendered="${tone}${text}${reset}"
        printf ' %s│%s%*s%s%*s%s│%s\n' \
            "$border" "$reset" "$l" '' "$rendered" "$r" '' "$border" "$reset"
    }

    banner_line ''
    for i in "${!logo[@]}"; do
        if ((i < 3)); then
            banner_line "${logo[i]}" "$cyan"
        else
            banner_line "${logo[i]}" "$light"
        fi
    done
    banner_line ''

    # Une seule signature, en vert. Les deux ⚙ restent jaunes.
    middle='⚙  AUTOKOM · Automation by IKOM  ⚙'
    row="${green}${middle}${reset}"
    row="${row//⚙/${yellow}⚙${green}}"
    banner_line "$middle" '' "$row"
    banner_line ''

    # Coins du bas arrondis, dans le prolongement exact des montants.
    printf ' %s╰%s╯%s\n\n' "$border" "$(repeat '─' "$inner")" "$reset"
}

banner
