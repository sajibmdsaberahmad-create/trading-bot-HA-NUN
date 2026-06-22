#!/bin/bash
# Launch Google Colab training notebook
# This script opens the colab_training.ipynb in your default browser

echo "========================================="
echo "  HA-NUN Grandmaster Training Launcher"
echo "========================================="
echo ""

# Check if colab_training.ipynb exists
if [ ! -f "colab_training.ipynb" ]; then
    echo "ERROR: colab_training.ipynb not found in current directory"
    echo "Please run this script from the repository root"
    exit 1
fi

# Get GitHub username from git remote
GITHUB_USERNAME=$(git remote get-url origin | sed -n 's/.*github.com\/\([^\/]*\)\/.*/\1/p')

if [ -z "$GITHUB_USERNAME" ]; then
    echo "WARNING: Could not detect GitHub username from git remote"
    echo "Please edit colab_training.ipynb and set GITHUB_USERNAME manually"
    GITHUB_USERNAME="YOUR_GITHUB_USERNAME"
else
    echo "Detected GitHub username: $GITHUB_USERNAME"
fi

# Update the notebook with correct username
sed -i.bak "s/YOUR_GITHUB_USERNAME/$GITHUB_USERNAME/g" colab_training.ipynb
rm -f colab_training.ipynb.bak

echo ""
echo "Opening Colab notebook..."
echo ""
echo "INSTRUCTIONS:"
echo "1. Upload colab_training.ipynb to Google Drive"
echo "2. Open with Google Colab"
echo "3. Run cells sequentially (1-7)"
echo "4. Training will run on GPU (A100/L4 on Colab Pro)"
echo ""
echo "Or use Google Colab CLI:"
echo "  colab upload colab_training.ipynb"
echo ""

# Try to open in browser (macOS/Linux)
if command -v open &> /dev/null; then
    open colab_training.ipynb
elif command -v xdg-open &> /dev/null; then
    xdg-open colab_training.ipynb
else
    echo "Please open colab_training.ipynb manually"
fi

echo "Done!"