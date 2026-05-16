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
| `deploy: `    | 배포 관련 설정 수정                            |
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

## 🔄 작업 흐름 예시
1. git checkout develop
2. git pull origin develop
3. git checkout feat/login
4. pip freeze > requirements.txt	
5. git commit -m "메시지"
6. git push
<br/>

