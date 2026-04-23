# QuickCut

브이로그 편집 시간을 줄이기 위한 **로컬 편집기**. 영상을 올리면 자막이 자동으로
만들어지고, 무음 구간을 찾아주고, 타이밍과 문장을 바로 고쳐서 한 번에 영상으로
뽑아준다.

— 계정·로그인·업로드·공유 기능 **없음**. 전부 내 컴퓨터 안에서만 돈다.

---

## Mac 설치 (5분)

### 1) Homebrew · ffmpeg · Python

**터미널** (⌘+Space → "터미널") 에서 한 줄씩 실행.

```bash
# Homebrew 가 없으면 먼저 설치
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# ffmpeg 와 Python 설치
brew install ffmpeg python
```

### 2) QuickCut 받기

```bash
cd ~/Downloads
git clone <리포 URL> quickcut
cd quickcut
```

### 3) 실행

```bash
./start.sh
```

처음 한 번은 Python 패키지 설치(~2분) + Whisper 모델 다운로드(460MB, ~5분)
하느라 시간이 걸린다. 이후부턴 몇 초 안에 뜸.

브라우저가 안 뜨면 **`http://localhost:7400`** 직접 접속.

---

## Windows 설치

관리자 PowerShell 에서:

```powershell
winget install Gyan.FFmpeg
winget install Python.Python.3.11
```

터미널 다시 열고:
```
pip install -r requirements.txt
start.bat
```

> Mac 이 주 환경이라 Windows 는 간소화. 문제 생기면 Mac 방식 권장.

---

## 사용 흐름

1. 메인에서 **영상 여러 개 끌어다 놓기** → "프로젝트 만들기"
2. **자동 분석** 버튼 → 자막 + 무음 감지 (영상 1개당 1–3분)
3. 각 클립에서
   - 자막 문장 타이핑으로 바로 수정 (오타 잡기)
   - 옆에 뜬 무음 구간 체크박스로 **스킵할지 남길지** 결정
   - 타임라인 핸들 끌어서 앞뒤 잘라내기
4. 왼쪽 사이드:
   - 자막 스타일 (미니멀 / 굵게 / 박스 / 상단)
   - 비율 (원본 / 9:16 세로)
5. **영상 내보내기** → 완성본 mp4 다운로드

---

## 할 수 있는 것 · 아직 없는 것

**Can**
- 여러 영상 한꺼번에
- 한국어 자동 자막 (오프라인)
- 무음 구간 자동 감지 + 선택 스킵
- 클립별 트림
- 자막 문장·시간 직접 수정
- 자막 스타일 4종
- 9:16 세로 내보내기
- H.264 / AAC (모든 기기 재생 OK)

**Not yet**
- 클립 순서 드래그 (파일 이름 순서대로 이어짐)
- 배경 음악
- 전환 효과 · 색보정
- 얼굴 트래킹 크롭

---

## 폴더 구조

```
quickcut/
├── app.py             Flask 서버
├── processor.py       Whisper + ffmpeg
├── silence.py         무음 감지
├── templates/         HTML
├── static/            CSS · JS
├── data/projects/     작업물 (git 제외)
├── requirements.txt
├── start.sh           Mac / Linux
├── start.bat          Windows
└── README.md
```

## 처음이 느린 이유

- Whisper 'small' 모델 460MB 한 번 받음
- CPU 전사라 영상 1개당 1–3분 (Apple Silicon 은 더 빠름)

## 용량 관리

`data/projects/` 에 원본·결과물이 쌓인다. 오래된 프로젝트는 메인에서 × 눌러
삭제.

## 안전

- **공용 네트워크에 노출하지 말 것.** `0.0.0.0` 바인딩이라 같은 Wi-Fi 의
  다른 기기에서도 `http://<Mac-IP>:7400` 으로 접속 가능.
