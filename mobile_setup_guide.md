# 📱 모바일(Termux) 구동 가이드 (Git 버전)

이 가이드는 PC에서 작업한 봇 코드를 안드로이드 폰(Termux)으로 가져와서 실행하는 방법입니다.
**파일 복사나 USB 연결 없이, GitHub를 통해 아주 쉽게 이동할 수 있습니다!**

## 1. 사전 준비 (폰에서)

### Termux 앱 설치
1.  구글 플레이스토어 또는 F-Droid에서 **Termux** 앱을 다운로드 및 설치합니다.
2.  앱을 실행하고 다음 명령어를 순서대로 입력하여 기본 패키지를 업데이트합니다.
    ```bash
    pkg update && pkg upgrade -y
    ```

### 필수 프로그램 설치 (Python, Git)
봇을 돌리는 데 필요한 Python과 코드를 가져올 Git을 설치합니다.
```bash
pkg install python git nano -y
```

---

## 2. 코드 가져오기 (GitHub에서) ☁️

PC에서 `git push`로 올린 코드를 폰으로 내려받습니다.

1.  **깃 클론 (내 봇 가져오기)**
    *   아래 `[당신의_깃허브_아이디]` 부분을 실제 아이디로 바꿔서 입력하세요.
    ```bash
    git clone https://github.com/[당신의_깃허브_아이디]/polymarket_bot.git
    ```

2.  **폴더로 이동**
    ```bash
    cd polymarket_bot
    ```

---

## 3. 가상환경 세팅 및 실행 🚀

폰에서도 컴퓨터처럼 '가상환경(Virtual Environment)'을 만들어줘야 오류 없이 깔끔하게 돌아갑니다.

### 1) 가상환경 생성 (한 번만)
```bash
python -m venv venv
```

### 2) 가상환경 켜기 (매번)
봇을 켤 때마다 이 명령어를 먼저 쳐야 합니다. (괄호 `(venv)`가 뜨면 성공!)
```bash
source venv/bin/activate
```

### 3) 필수 재료 설치 (한 번만)
`requirements.txt`에 적힌 목록대로 라이브러리를 설치합니다.
```bash
pip install -r requirements.txt
```

### 4) API 키 설정 (.env 파일 생성) 🔑
보안상 깃허브에는 API 키가 올라가지 않았으므로, 폰에서 직접 만들어줘야 합니다.

1.  에디터 열기:
    ```bash
    nano .env
    ```
2.  **내용 붙여넣기**: PC에 있는 `.env` 파일 내용을 복사해서 여기 붙여넣으세요.
    *   화면을 꾹 누르면 'Paste(붙여넣기)' 메뉴가 뜹니다.
3.  **저장하고 나가기**:
    *   `Ctrl` + `o` 누르고 `Enter` (저장)
    *   `Ctrl` + `x` (나가기)

> [!IMPORTANT]
> **가상 매매(Simulation) 확인**: `.env` 파일 안에 `PAPER_TRADING=True` 라고 되어 있는지 꼭 확인하세요! (기본값은 True지만 확실한 게 좋으니까요!)

---

## 4. 봇 실행! 🔥

준비가 끝났습니다. 이제 돈 벌러 가봅시다!

```bash
python main.py
```

---

### 💡 꿀팁: 다음에 다시 실행할 때는?

다음에 앱을 껐다 켰을 때는 이것만 기억하세요:

```bash
# 1. 폴더 이동
cd polymarket_bot

# 2. 가상환경 켜기
source venv/bin/activate

# 3. 실행
python main.py
```