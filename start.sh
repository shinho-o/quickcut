#!/usr/bin/env bash
# QuickCut — macOS / Linux 실행 스크립트
set -e
cd "$(dirname "$0")"

echo ""
echo "  QuickCut 시작 준비..."
echo ""

# ─── 필수 프로그램 확인 ───
need_brew=""
if ! command -v ffmpeg >/dev/null 2>&1; then need_brew="$need_brew ffmpeg"; fi
if ! command -v python3 >/dev/null 2>&1; then need_brew="$need_brew python"; fi

if [ -n "$need_brew" ]; then
    echo "먼저 설치가 필요한 프로그램:$need_brew"
    echo ""
    if [ "$(uname)" = "Darwin" ]; then
        echo "  1) Homebrew 가 없으면 먼저 설치:"
        echo "     /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        echo ""
        echo "  2) 그 다음:"
        echo "     brew install$need_brew"
    else
        echo "  Ubuntu/Debian:"
        echo "     sudo apt update && sudo apt install -y$need_brew"
    fi
    echo ""
    echo "설치 후 다시 ./start.sh 실행해주세요."
    exit 1
fi

# ─── Python 패키지 확인 / 설치 ───
if ! python3 -c "import flask, waitress, faster_whisper, librosa" 2>/dev/null; then
    echo "Python 패키지 설치 중... (처음 한 번만, 수 분 걸릴 수 있음)"
    python3 -m pip install --user -r requirements.txt
    echo ""
fi

echo "준비 완료. 브라우저에서  http://localhost:7400  열기"
echo "Mac 이면 잠시 뒤 자동으로 열립니다."
echo ""

# Mac: 브라우저 자동 열기 (서버가 뜰 때까지 4초 대기)
if [ "$(uname)" = "Darwin" ]; then
    (sleep 4 && open "http://localhost:7400") &
fi

exec python3 app.py
