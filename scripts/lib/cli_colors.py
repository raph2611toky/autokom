"""Small ANSI layer for AUTOKOM status output; never modifies redirected output."""
import os
import re
import sys

_PALETTE = {"✓":"\033[38;2;134;239;172m", "✗":"\033[38;2;255;135;135m",
            "!":"\033[38;2;255;211;120m", "›":"\033[38;2;125;211;252m",
            "+":"\033[38;2;134;239;172m", "◈":"\033[1;36m"}
_PREFIX=re.compile(r"^(\s*)([✓✗!›+◈])(?=\s|$)")

def pretty_print(*args, sep=' ', end='\n', file=None, flush=False):
    target=file if file is not None else sys.stdout
    raw=sep.join(str(arg) for arg in args)
    enabled=(getattr(target,'isatty',lambda:False)() and 'NO_COLOR' not in os.environ
             and os.environ.get('TERM','') not in ('','dumb'))
    if enabled:
        raw='\n'.join(_PREFIX.sub(lambda m:m.group(1)+_PALETTE[m.group(2)]+m.group(2)+'\033[0m',line,count=1)
                      for line in raw.split('\n'))
    target.write(raw+end)
    if flush: target.flush()
