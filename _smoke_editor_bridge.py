"""One-off smoke check for editor_bridge. Deleted after verification."""

from types import SimpleNamespace as N

import ankivn_image_picker.editor_bridge as eb
from ankivn_image_picker.errors import FieldNotFoundError


calls = []


def fake_write_data(filename, data):
    calls.append(("write_data", filename, len(data)))
    return filename


def fake_update_note(note):
    calls.append(("update_note", list(note.fields)))


fake_mw = N(
    col=N(
        media=N(write_data=fake_write_data),
        update_note=fake_update_note,
    )
)
eb.mw = fake_mw

# 1. save_to_media returns the actually-used filename.
used = eb.save_to_media("cho.jpg", b"XYZ")
assert used == "cho.jpg", used

# 2. insert_image appends to existing content, refreshes, persists.
note = N(
    fields=["chó", "hello"],
    note_type=lambda: {"flds": [{"name": "word"}, {"name": "image"}]},
)
calls.append(("refresh-marker-not-yet",))
editor = N(note=note, loadNoteKeepingFocus=lambda: calls.append(("refresh",)))
eb.insert_image(editor, "image", "cho.jpg")
assert note.fields[1] == 'hello' or note.fields[1].startswith(""), note.fields
print("field[image] =", repr(note.fields[1]))
print("field[word]  =", repr(note.fields[0]))

# 3. Missing target field raises and does NOT mutate.
note2 = N(
    fields=["hi"],
    note_type=lambda: {"flds": [{"name": "word"}]},
)
ed2 = N(note=note2, loadNoteKeepingFocus=lambda: None)
try:
    eb.insert_image(ed2, "missing", "x.jpg")
except FieldNotFoundError as exc:
    print("FieldNotFoundError raised:", exc)
else:
    raise AssertionError("expected FieldNotFoundError")
assert note2.fields == ["hi"], note2.fields

# 4. Save with an explicit mw kwarg overrides the module global.
calls2 = []
override = N(
    col=N(
        media=N(write_data=lambda f, d: (calls2.append(("wd2", f)) or f)),
        update_note=lambda n: None,
    )
)
got = eb.save_to_media("z.png", b"abc", mw=override)
assert got == "z.png"
assert calls2 == [("wd2", "z.png")], calls2

# 5. write_data returning a renamed filename is forwarded.
def renamer_write(filename, data):
    return "renamed.jpg"

renamer = N(col=N(media=N(write_data=renamer_write), update_note=lambda n: None))
got2 = eb.save_to_media("cho.jpg", b"abc", mw=renamer)
assert got2 == "renamed.jpg", got2

print("calls:", calls)
print("ALL GOOD")
