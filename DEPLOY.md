# 배포 가이드

## GitHub
기준 저장소: `gt10300407/matjib`

실제 API 키, `.env`, SQLite DB는 GitHub에 올리지 않는다.

## Render 1차 공개 배포
1. Render Dashboard → New → Blueprint.
2. GitHub 저장소 `gt10300407/matjib` 연결.
3. 저장소 루트의 `render.yaml`을 사용.
4. 아래 Secret 값을 Render Dashboard에서 입력.
   - `KAKAO_REST_API_KEY`
   - `DATA_GO_KR_SERVICE_KEY`
   - `TOUR_API_SERVICE_KEY`
5. 배포 완료 후 Render가 발급한 `https://...onrender.com` 주소를 공유.

### 중요
현재 Blueprint의 `plan: free`는 1차 링크 공유/테스트용이다. Render Free Web Service의 로컬 파일시스템은 재시작/재배포 시 유지되지 않으므로, SQLite 캐시는 영구 DB로 보지 않는다.

다음 배포 단계에서 PostgreSQL을 연결해 맛집 캐시/변경 이력을 영구 저장하는 구조로 전환한다.
