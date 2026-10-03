"""The program to model: read stdin text and write a transformed form to stdout."""

import sys

data = sys.stdin.read()
out = []
i = 0
while i < len(data):
    j = i
    while j < len(data) and data[j] == data[i]:
        j += 1
    char = data[i]
    out.append(f"{j - i}{char}" if j - i > 1 else char)
    i = j
sys.stdout.write("".join(out))
