# 유인호 최신뉴스 텔레그램 봇

유인호 세종특별자치시의원 관련 최신 뉴스를 자동으로 수집하여 텔레그램으로 전송하는 봇입니다.  
GitHub Actions를 통해 하루 2회 자동 실행되며, 네이버 뉴스 API로 기사를 수집합니다.

---

## 주요 기능

- **자동 뉴스 수집**: 네이버 뉴스 Open API를 통해 최신 기사를 자동으로 검색
- **중복 방지**: 이미 발송한 기사는 다시 보내지 않음
- **동명이인 필터**: "유인호" 이름이 들어가도 세종시 관련 기사만 선별 전송
- **오류 알림**: 봇 실행 중 오류 발생 시 텔레그램으로 즉시 알림
- **구독 관리**: `/subscribe`, `/unsubscribe` 명령어로 구독 신청·취소 가능
- **하루 2회 실행**: 오전 8시, 오후 6시 (KST) 자동 실행

---

## 기술 스택

| 항목 | 내용 |
|---|---|
| 언어 | Python 3.12 |
| 뉴스 수집 | 네이버 뉴스 Open API |
| 메시지 전송 | Telegram Bot API (`requests` 직접 호출) |
| 자동화 | GitHub Actions (cron 스케줄) |
| 상태 저장 | `seen_urls.txt`, `subscribers.json` (git 커밋으로 유지) |

---

## 프로젝트 구조

```
유인호 최신뉴스/
├── check_and_send.py         # 메인 실행 파일
├── seen_urls.txt             # 발송 완료된 기사 URL 목록 (중복 방지용)
├── subscribers.json          # 구독자 chat_id 및 텔레그램 offset 저장
└── .github/
    └── workflows/
        └── news_check.yml    # GitHub Actions 워크플로우 설정
```

---

## 실행 흐름

```
GitHub Actions 실행 (오전 8시 / 오후 6시 KST)
       │
       ▼
구독자 목록 로드 & 텔레그램 커맨드 처리 (/subscribe 등)
       │
       ▼
네이버 뉴스 API 검색 (3가지 쿼리)
  - "유인호 세종특별자치시의원"
  - "유인호 세종 후보"
  - "유인호 세종시의회"
       │
       ▼
기사 필터링
  - 최근 15시간 이내 기사만 허용
  - "유인호" + 세종시 관련 키워드 포함 여부 확인
  - 동명이인(배우, 가수, 교수 등) 제외
  - 이미 발송한 기사(seen_urls) 제외
       │
       ▼
새 기사 있음 → 텔레그램 전송
새 기사 없음 → "현재까지 최신기사 없음" 메시지 전송
       │
       ▼
seen_urls.txt 업데이트 후 GitHub에 커밋·푸시
```

---

## GitHub Actions 설정 (`news_check.yml`)

```yaml
on:
  schedule:
    - cron: '0 23 * * *'   # 매일 오전 8시 KST (UTC 23:00)
    - cron: '0 9 * * *'    # 매일 오후 6시 KST (UTC 09:00)
  workflow_dispatch:         # 수동 실행 가능

concurrency:
  group: news-check
  cancel-in-progress: false  # 동시 실행 방지 (중복 발송 예방)
```

> **참고**: GitHub Actions는 서버 부하에 따라 실제 실행이 10~30분 늦어질 수 있습니다.

---

## 환경 변수 (GitHub Secrets)

GitHub 저장소 → Settings → Secrets and variables → Actions에서 아래 3가지를 등록해야 합니다.

| Secret 이름 | 설명 |
|---|---|
| `BOT_TOKEN` | 텔레그램 봇 토큰 (`@BotFather`에서 발급) |
| `NAVER_CLIENT_ID` | 네이버 개발자센터 애플리케이션 Client ID |
| `NAVER_CLIENT_SECRET` | 네이버 개발자센터 애플리케이션 Client Secret |

---

## 텔레그램 봇 명령어

| 명령어 | 설명 |
|---|---|
| `/start` | 봇 소개 및 사용 방법 안내 |
| `/subscribe` | 뉴스 알림 구독 시작 |
| `/unsubscribe` | 구독 취소 |
| `/status` | 현재 구독 상태 확인 |

---

## 기사 필터 상세

### 필수 키워드 (하나 이상 포함 필요)
```
세종, 세종시, 세종특별자치시, 세종시의원, 세종특별자치시의원,
세종시의회, 민주당, 더불어민주당, 보람동
```

### 제외 키워드 (세종 관련 내용 없을 경우 제외)
```
배우, 가수, 감독, 작가, 교수, 부산, 대구, 인천, 광주, 대전, 울산, 수원, 성남, 고양
```

---

## 알림 메시지 형식

### 새 기사 발견 시
```
🔔 새 뉴스 알림
━━━━━━━━━━━━━━━━━━
📅 2026년 06월 14일 08:58

📌 제5대 세종시의회, 7월 출범 향해...

📝 세종특별자치시의회가 제5대 의회 개원을 눈앞에 두고...

🔗 https://...
```

### 새 기사 없을 시
```
📭 2026년 06월 14일 09:08 기준
현재까지 최신기사는 없습니다.
```

### 오류 발생 시
```
⚠️ 봇 오류 발생
━━━━━━━━━━━━━━━━━━
🕐 2026년 06월 14일 09:08

ConnectionError: ...
```

---

## 중복 발송 방지 구조

1. **`seen_urls.txt`**: 발송한 기사의 URL을 저장하여 다음 실행 시 제외  
2. **URL 정규화**: 트래킹 파라미터(`utm_source`, `fbclid` 등)는 제거하되, 기사 식별자(`idxno`, `key` 등)는 유지  
3. **실행 내 중복 제거**: 같은 실행에서 3가지 검색 쿼리 결과 간 중복 제거  
4. **동시 실행 방지**: GitHub Actions `concurrency` 설정으로 두 run이 겹치지 않도록 보장  

---

## 로컬 실행 방법 (테스트용)

```bash
# 환경 변수 설정 후 실행
export BOT_TOKEN="your_bot_token"
export NAVER_CLIENT_ID="your_client_id"
export NAVER_CLIENT_SECRET="your_client_secret"

pip install requests
python check_and_send.py
```

---

## 주의사항

- `seen_urls.txt`와 `subscribers.json`은 GitHub Actions가 자동으로 커밋·관리합니다. 직접 수정 시 다음 실행에 영향을 줄 수 있습니다.
- 네이버 뉴스 Open API는 하루 25,000건 호출 제한이 있습니다. 현재 하루 6회 API 호출(쿼리 3개 × 실행 2회)로 여유 있게 사용 중입니다.
- GitHub Actions 무료 플랜은 월 2,000분 제공됩니다. 1회 실행 약 30초 × 60회/월 = 약 30분 사용으로 여유 있게 유지됩니다.
