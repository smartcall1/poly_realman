# Polymarket Whale Copy Trading Bot - 모바일 (Termux) 환경 설정 가이드

본 문서는 안드로이드 스마트폰에 Termux 애플리케이션을 설치하여 Polymarket Whale Copy Trading Bot을 24시간 가동하기 위한 설정 방법을 안내합니다.

## 1. Termux 초기 설정

앱 설치 직후 기본 패키지와 시스템 빌드 도구들을 설치해야 합니다.

```bash
# 1. 기기 저장소 접근 권한 부여
termux-setup-storage

# 2. 패키지 목록 갱신
pkg update -y && pkg upgrade -y

# 3. 필수 빌드 도구 및 파이썬 패키지 설치
pkg install python git clang make libffi openssl rust binutils requests -y
```
*참고: 빌드 패키지(`clang`, `rust`, `make` 등)는 API 암호화 라이브러리인 `py-clob-client`를 컴파일하기 위해 반드시 필요합니다.*

## 2. 소스 코드 동기화 및 가상환경 세팅

PC에서 개발한 봇의 최신 버전을 모바일 환경으로 가져오고 독립된 파이썬 환경을 구성합니다.

```bash
# 1. 깃 저장소 클론 (본인의 계정 URL로 변경)
git clone https://github.com/[당신의_깃허브_아이디]/polymarket_bot.git

# 2. 작업 폴더로 이동
cd polymarket_bot

# 3. 파이썬 가상환경 생성 및 활성화
python -m venv venv
source venv/bin/activate

# 4. 필수 라이브러리 설치
pip install --upgrade pip
pip install -r requirements.txt
```

## 3. 환경 변수(.env) 구성

로컬 환경 변수 파일은 깃허브에 공유되지 않으므로, 폰에서 직접 생성하여 내용을 붙여넣어야 합니다.

```bash
# 에디터 실행
nano .env
```
PC에 설정되어 있는 `.env` 파일의 내용(PAPER_TRADING, API Key, Wallet 정보 등)을 복사하여 Termux 화면을 길게 눌러 `Paste` 합니다.
작성이 끝나면 `Ctrl + O` (저장) -> `Enter` -> `Ctrl + X` (종료) 순서로 에디터를 빠져나옵니다.

## 4. 백그라운드 봇 실행

모바일 기기의 화면이 꺼지면 운영체제가 백그라운드 앱을 종료할 수 있습니다. 이를 방지하고 봇을 실행하는 방법입니다.

```bash
# 1. CPU 절전 모드 방지 (안드로이드 시스템에 백그라운드 유지 요청)
termux-wake-lock

# 2. 메인 봇 실행 (고래 매니저 및 스코어러 스레드 자동 포함)
python whale_copy_bot.py
```
*(성공적으로 적용되었다면 알림 창에 'Termux Wake lock held' 상태가 유지됩니다.)*

## 5. 재실행 요약 (앱 재가동 시)

향후 기기를 재부팅하거나 앱을 재시작했을 때 사용할 명령어 요약입니다.

```bash
termux-wake-lock
cd polymarket_bot
source venv/bin/activate
python whale_copy_bot.py
```

## 문제 해결 (Troubleshooting)

**1. 패키지 빌드 오류 발생 시 (`Rust` 또는 `Compiler` 관련 로그)**
모바일 CPU 아키텍처 특성상 간혹 빌드 패키지 오류가 발생할 수 있습니다. 아래 명령어로 필수 빌드 패키지들이 정확히 설치되었는지 재확인하시기 바랍니다.
`pkg install rust binutils libffi openssl -y`

**2. 모듈이 없다는 에러 발생 시**
가상환경(`venv`)이 활성화되어 있는지 프롬프트 창을 확인하시고, `pip install -r requirements.txt` 명령어를 통해 재설치 하십시오.
