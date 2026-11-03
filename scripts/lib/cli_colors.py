"""Small ANSI layer for AUTOKOM status output; never modifies redirected output."""
import os
import re
import sys

_PALETTE = {"✓":"\033[38;2;134;239;172m", "✗":"\033[38;2;255;135;135m",
            "!":"\033[38;2;255;211;120m", "›":"\033[38;2;125;211;252m",
            "+":"\033[38;2;134;239;172m", "◈":"\033[1;36m"}
_PREFIX=re.compile(r"^(\s*)([✓✗!›+◈])(?=\s|$)")
# Only color *exact* status tokens; never recolor URLs, paths or arbitrary log text.
_STATUSES=re.compile(r"(?<![\w/.-])(OK|PASS|UP|DOWN|FAIL|FAILED|ABSENTE|ABSENT|ERREUR|ERROR|WARNING)(?![\w/.-])")
_STATUS_COLORS={
    'OK':'✓','PASS':'✓','UP':'✓',
    'DOWN':'✗','FAIL':'✗','FAILED':'✗','ABSENTE':'✗','ABSENT':'✗','ERREUR':'✗','ERROR':'✗',
    'WARNING':'!',
}

def pretty_print(*args, sep=' ', end='\n', file=None, flush=False):
    target=file if file is not None else sys.stdout
    raw=sep.join(str(arg) for arg in args)
    enabled=(getattr(target,'isatty',lambda:False)() and 'NO_COLOR' not in os.environ
             and os.environ.get('TERM','') not in ('','dumb'))
    if enabled:
        def style(line):
            line=_PREFIX.sub(lambda m:m.group(1)+_PALETTE[m.group(2)]+m.group(2)+'\033[0m',line,count=1)
            return _STATUSES.sub(lambda m:_PALETTE[_STATUS_COLORS[m.group(1)]]+m.group(1)+'\033[0m',line)
        raw='\n'.join(style(line) for line in raw.split('\n'))
    target.write(raw+end)
    if flush: target.flush()
