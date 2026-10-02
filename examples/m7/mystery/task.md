# Model the mystery program

`mystery.py` reads text on stdin and writes a transformed form to stdout. Work out
exactly what it does by running it, for example:

    printf 'hello' | python mystery.py

Then write `result.json` containing a JSON object with two fields:

- `model`: the complete source of a Python program that reads stdin and writes
  stdout exactly as `mystery.py` does, for every input.
- `cases`: up to 16 input strings (at most 256 characters each) that you want
  checked first. Failures on these cases are reported back to you in detail.

The checker also runs inputs you have not seen. For those you only learn how many
failed, never which inputs they were.

Submit by printing `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` on the first stdout line
of your final command. Earlier feedback, if any, is in `feedback-NNN.json`.
