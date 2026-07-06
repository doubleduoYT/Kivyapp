# HG OS 2 Beta (v2.0.6) - PyGame Port

Entry 작품을 PyGame에서 실행하도록 변환한 버전이야.
이번 버전은 FastEntry 코드 쪽 동작을 참고해서 소리 반복/중첩과 렌더링 부하를 줄였어.

## 실행

```bash
python -m pip install -r requirements.txt
python main.py
```

기본 창 크기는 Entry 무대의 2배인 `960x540`이야.

```bash
python main.py --scale 2      # 960x540
python main.py --scale 1      # 480x270
python main.py --debug        # 디버그 표시
```

## 이번 업그레이드

- FastEntry의 `playBackgroundMusic` 동작처럼 BGM을 무한 반복하지 않게 수정
- 오브젝트별 효과음 채널을 관리해서 같은 효과음이 반복문에서 계속 겹쳐 재생되는 문제 완화
- `sound_silent_all`의 `thisOnly / all` 처리 분리
- `sound_something_second_wait_with_block` 추가 지원
- 소리 ID뿐 아니라 이름/파일명/1부터 시작하는 번호로도 소리를 찾도록 보강
- 이미지 scale/flip/rotation/alpha/brightness 변환 캐시 추가
- 방송/장면 시작/클릭 이벤트 hat 캐시 추가
- 장면 전환 시 이전 장면 효과음 정리

## 조작

- 마우스 클릭: Entry 클릭 이벤트
- 입력창이 뜨면 글자 입력 후 Enter
- Esc: 종료
- Ctrl+R: 프로젝트 재시작

AI, 음성 인식, 카메라, 실시간 날씨/미세먼지 같은 외부 기능은 안전한 더미값 또는 no-op으로 처리돼.

## 2026-07 home click/audio optimization patch
- Fixed home-button clicks in app scenes such as weather/music/camera by dispatching clicks to all clickable objects under the pointer instead of only the topmost bounding box.
- Added alpha-based hit testing for transparent PNG areas.
- Kept 960x540 default window and previous FastEntry-style audio/image caches.
