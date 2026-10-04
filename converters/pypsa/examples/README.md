# Examples

All data here are synthetic: invented numbers and smooth deterministic curves.

| File | What it is |
|---|---|
| `toy_network/` | A PyPSA CSV folder: four electrical buses and one hydrogen bus, 24 hourly snapshots each standing for 365 hours. It deliberately includes features that do not map one to one (a transformer, a sector-coupling link, a store, reservoir hydro, a must-run unit, several loads on one bus) so the conversion report has something to say. |
| `make_toy_network.py` | Regenerates `toy_network/` (PyPSA not needed). |
| `convert_toy.py` | Converts the toy network to a CE bundle and back, printing both conversion reports. |

```bash
python make_toy_network.py           # rewrite toy_network/
python convert_toy.py /tmp/ce_toy    # writes /tmp/ce_toy/toy_bundle and /tmp/ce_toy/toy_back
```
