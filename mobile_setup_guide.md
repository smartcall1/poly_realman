# 📱 POLYMARKET HATEBOT - 모바일(Termux) 완벽 구동 가이드

> **"지상 최강 트레이더를 내 주머니 속에."**
> 이 가이드는 안드로이드 폰(Termux)에서 봇을 완벽하게 구동하기 위한 **All-in-One** 매뉴얼입니다.

---

## 🚀 1. Termux 기초 공사 (필수)

Termux를 처음 설치했다면, 봇이 돌아갈 수 있는 '기초 체력'을 길러줘야 합니다.  
아래 명령어들을 **한 줄씩 복사**해서 Termux에 붙여넣고 엔터(Enter)를 치세요. 중간에 `Example... [Y/n]` 처럼 물어보면 무조건 **`y`** 를 누르고 엔터!

### 1️⃣ 패키지 업데이트 & 권한 설정
```bash
# 저장소 접근 권한 허용 (필수)
termux-setup-storage

# 패키지 목록 최신화 및 업그레이드
pkg update -y && pkg upgrade -y
```

### 2️⃣ 필수 시스템 패키지 설치 (중요 ⭐)
암호화 라이브러리(`py-clob-client`) 구동을 위해 컴파일러와 라이브러리가 꼭 필요합니다.  
**이 단계가 누락되면 설치 도중 에러가 납니다!**

```bash
# 빌드 도구 및 라이브러리 일괄 설치
pkg install python git clang make libffi openssl rust binutils requests -y
```
*(설치 시간이 3~5분 정도 걸릴 수 있습니다. 인내심을 가지세요!)*

---

## ☁️ 2. 소스 코드 가져오기

PC에서 작업한 최신 코드를 폰으로 가져옵니다.

1. **깃 클론 (내 저장소 복제)**
   * `[당신의_깃허브_아이디]`를 본인 ID로 바꿔주세요. (예: `smartcall1`)
   ```bash
   git clone https://github.com/[당신의_깃허브_아이디]/polymarket_bot.git
   ```

2. **폴더로 이동**
   ```bash
   cd polymarket_bot
   ```

---

## ⚡ 3. 파이썬 가상환경 세팅

시스템 파이썬을 더럽히지 않고 깔끔하게 돌리기 위해 가상환경을 만듭니다.

### 1️⃣ 가상환경 생성 (최초 1회)
```bash
python -m venv venv
```

### 2️⃣ 가상환경 켜기 (매번)
봇을 켤 때마다 이 명령어를 먼저 입력해야 합니다. 프롬프트 앞에 `(venv)`가 뜨면 성공!
```bash
source venv/bin/activate
```

### 3️⃣ 라이브러리 설치 (최초 1회)
방금 설치한 시스템 패키지 덕분에 에러 없이 잘 깔릴 겁니다.
```bash
# 최신 pip 업그레이드
pip install --upgrade pip

# 봇 구동 라이브러리 설치
pip install -r requirements.txt
```

---

## 🔑 4. API 키 설정 (보안 필수)

GitHub에는 비밀번호(`.env`)가 없으므로 폰에서 직접 만들어줘야 합니다.

1. **에디터 열기**
   ```bash
   nano .env
   ```

2. **내용 붙여넣기**
   - PC에 있는 `.env` 내용을 전부 복사하세요.
   - Termux 화면을 **길게 꾹 누르고** `Paste`를 선택합니다.

3. **저장하고 나오기**
   - 키보드 위 `CTRL` 버튼을 누른 상태에서 `o` (저장) -> `Enter`
   - 키보드 위 `CTRL` 버튼을 누른 상태에서 `x` (종료)

---

## 🔥 5. 지상 최강 트레이더 실행!

준비는 끝났습니다. 이제 24시간 자동 사냥을 시작합니다.

### 1️⃣ 화면 꺼짐 방지 (필수)
폰 화면이 꺼져도 봇이 죽지 않게 락을 겁니다.
```bash
termux-wake-lock
```
*(알림바에 'Termux Wake lock held'라고 뜨면 성공)*

### 2️⃣ 봇 실행
```bash
python main.py
```

---

## 💡 요약: 나중에 다시 켤 때는?

앱을 껐다가 다시 켤 때는 **이것만 기억하세요!**

```bash
# 1. 방해 금지 모드 (Wake Lock)
termux-wake-lock

# 2. 폴더 이동
cd polymarket_bot

# 3. 가상환경 켜기
source venv/bin/activate

# 4. 봇 실행
python main.py
```


---

## ❓ 자주 묻는 질문 (Troubleshooting)

### Q. 'ModuleNotFoundError: No module named requests' 에러가 떠요!
설치 도중 인터넷이 끊겼거나 일부 패키지 설치가 실패해서 그럴 수 있습니다. 당황하지 말고 아래 명령어를 입력하세요.

```bash
# requests 모듈 수동 설치
pip install requests

# 전체 의존성 다시 확실하게 설치
pip install -r requirements.txt
```

### Q. 'Rust'나 'Cargo' 관련 에러가 빨간색으로 떠요!
암호화 라이브러리 빌드에 필요한 도구가 없는 경우입니다. 1번 단계의 **필수 시스템 패키지 설치**를 다시 한 번 실행해주세요.
```bash
pkg install rust binutils libffi openssl -y
```
