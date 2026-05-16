 ## ⚙️ 기술 스택
- Language: Python, LangGraph
- Framework: FastAPI
- Database: PostgreSQL

<br/>

## 💬 커밋 메시지 컨벤션
| Tag              | Description                                 |
|------------------|---------------------------------------------|
| `feat: `      | 새로운 기능 추가                             |
| `fix: `       | 버그 수정                                    |
| `docs: `      | 문서 수정                                    |
| `test: `      | 테스트 코드 추가 또는 수정                    |
| `style: `     | 코드 포맷팅, 세미콜론 누락 등 (로직 변경 없음) |
| `refactor: `  | 코드 리팩토링                                |
| `perf: `      | 성능 개선                                    |
| `build: `     | 빌드 시스템 또는 의존성 변경                  |
| `ci: `        | CI 관련 설정 수정                            |
| `chore: `     | 설정 파일, 기타 잡일                         |
<br/>

## 🗂️ 프로젝트 구조

```
tripto/
├── .github/             
├── app/                  
│   ├── agent/            
│   ├── api/              
│   ├── core/             
│   ├── infra/            
│   ├── models/           
│   ├── schemas/          
│   ├── services/         
│   └── main.py                     
├── .gitignore            
├── Dockerfile
├── docker-compose.yml
└── requirements.txt      
```

<br/>

## 🌱 브랜치 전략
| Branch     |             Description        |
|------------|--------------------------------|
| `main`     | 항상 배포 가능한 상태의 코드 유지 |
| `develop`  | 개발 중인 기능을 통합하는 브랜치  |
<br/>

## 🔄 브랜치 흐름
1. develop 브랜치 최신화 git pull origin develop
2. 모든 작업은 `develop`에서 기능/이슈 단위로 브랜치 생성
3. 의존성 최신화 pip freeze > requirements.txt  
4. 작업 완료 후 `develop`으로 Pull Request(PR) 생성
<br/>


## 🧾 이슈 & PR 규칙
### 📌 이슈 규칙  
- **제목**: `[타입] 이슈 설명`
  - 예: `Feat: 회원가입 기능 구현`

- **내용**: 작업 내용 명시

- **라벨 / Assignee**: 필수 지정
<br/>

### 🔀 Pull Request 규칙
- **제목**: `타입: PR 설명 (#이슈번호)`
  - 예: `Feat: 회원가입 기능 추가 (#12)`

- **설명**:
  - 해결한 이슈 번호
  - 작업 내용 요약

- **Merge 조건**:
  - 코드 리뷰 승인
  - CI 테스트 통과

- **이슈와 연동** : PR이 관련 이슈를 닫도록 연결하거나 직접 닫기 
 <br/>

