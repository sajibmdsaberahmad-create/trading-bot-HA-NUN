# Colab Session Fix — Run This Now

Use this if Cell 2 failed because the repo wasn’t cloned. Paste into a fresh Colab cell after Drive is mounted.

```python
%cd /content/drive/MyDrive/
!git clone https://github.com/sajibmdsaberahmad-create/trading-bot-HA-NUN.git
%cd trading-bot-HA-NUN
!git pull origin main
!ls -la
!test -f main.py && echo "Repo ready" || echo "Missing main.py"
```

Then continue with the existing notebook cells 3–9.

If the repo is private, rerun cell 2 with a token:
```python
%cd /content/drive/MyDrive/
TOKEN = "YOUR_GITHUB_PAT"
!git clone https://{TOKEN}@github.com/sajibmdsaberahmad-create/trading-bot-HA-NUN.git
%cd trading-bot-HA-NUN
```

After that, run Cell 3 (`pip install -r requirements.txt`) and continue with training.