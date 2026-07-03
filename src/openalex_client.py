"""
src/openalex_client.py
OpenAlex API로 논문 인용 관계를 조회합니다.
- DOI → OpenAlex Work (referenced_works 포함)
- 결과를 JSON 캐시에 저장하여 재실행 시 API 재호출 없음
"""
import json
import os
import time

import requests
from tqdm import tqdm


class OpenAlexClient:
    BASE_URL = "https://api.openalex.org"

    def __init__(self, email: str = '', cache_file: str = 'cache/openalex_cache.json'):
        self.email = email
        self.cache_file = cache_file
        self.cache: dict = self._load_cache()

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'CitationNetworkBuilder/1.0 (https://github.com/idlhy0218/Citation-Network)',
        })
        # Polite pool: 이메일 등록 시 더 높은 rate limit
        if email:
            self.session.params = {'mailto': email}

    # ------------------------------------------------------------------ #
    # 캐시
    # ------------------------------------------------------------------ #
    def _load_cache(self) -> dict:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_cache(self) -> None:
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------ #
    # HTTP
    # ------------------------------------------------------------------ #
    def _get(self, url: str, params: dict | None = None, retries: int = 3):
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=20)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 404:
                    return None
                if resp.status_code == 429:
                    wait = 15 * (attempt + 1)
                    print(f"\n  ⚠️  Rate limit (429). {wait}초 대기...")
                    time.sleep(wait)
                else:
                    print(f"\n  ⚠️  HTTP {resp.status_code} for {url}")
                    time.sleep(2)
            except requests.exceptions.Timeout:
                print(f"\n  ⚠️  Timeout (시도 {attempt+1}/{retries})")
                time.sleep(3)
            except requests.exceptions.RequestException as e:
                print(f"\n  ⚠️  요청 에러: {e}")
                time.sleep(3)
        return None

    # ------------------------------------------------------------------ #
    # DOI → OpenAlex Work
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize_doi(doi: str) -> str:
        doi = doi.lower().strip()
        doi = doi.replace('https://doi.org/', '').replace('http://doi.org/', '').strip('/')
        return doi

    def get_work_by_doi(self, doi: str) -> dict | None:
        """DOI로 OpenAlex Work 조회 (캐시 사용)"""
        if not doi:
            return None

        doi_norm = self._normalize_doi(doi)
        cache_key = f"doi:{doi_norm}"

        if cache_key in self.cache:
            return self.cache[cache_key]

        data = self._get(f"{self.BASE_URL}/works/doi:{doi_norm}")
        self.cache[cache_key] = data     # None도 저장 (재조회 방지)
        time.sleep(0.12)                 # ~8 req/sec
        return data

    # ------------------------------------------------------------------ #
    # Abstract 재구성 (OpenAlex inverted index → 평문)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _reconstruct_abstract(inverted_index: dict) -> str:
        if not inverted_index:
            return ''
        word_positions: list[tuple[int, str]] = []
        for word, positions in inverted_index.items():
            for pos in positions:
                word_positions.append((pos, word))
        word_positions.sort(key=lambda x: x[0])
        return ' '.join(w for _, w in word_positions)

    # ------------------------------------------------------------------ #
    # 인용 네트워크 구축
    # ------------------------------------------------------------------ #
    def build_citation_network(
        self,
        papers_by_collection: dict,
    ) -> tuple[dict, dict, dict]:
        """
        컬렉션별 논문 dict를 받아 인용 관계를 구축합니다.

        반환:
            cites      : {doi: [doi, ...]}   — 이 논문이 인용한 컬렉션 내 논문 DOI
            cited_by   : {doi: [doi, ...]}   — 이 논문을 인용한 컬렉션 내 논문 DOI
            all_papers : {doi: paper_meta}   — doi 키로 통합된 논문 정보
        """
        # 모든 논문을 doi → meta 로 통합
        all_papers: dict[str, dict] = {}
        for col_data in papers_by_collection.values():
            for paper in col_data['papers']:
                doi = paper.get('doi', '')
                if doi:
                    all_papers[doi] = paper

        print(f"\n🔍 OpenAlex에서 {len(all_papers)}개 논문 인용 관계 조회 중...")

        # Step 1: 각 DOI → OpenAlex ID 매핑
        doi_to_oa_id: dict[str, str] = {}
        oa_id_to_doi: dict[str, str] = {}
        doi_to_referenced: dict[str, list[str]] = {}

        no_doi_papers = [p for col in papers_by_collection.values()
                         for p in col['papers'] if not p.get('doi')]
        if no_doi_papers:
            print(f"  ⚠️  DOI 없는 논문 {len(no_doi_papers)}개 스킵")

        for doi in tqdm(list(all_papers.keys()), desc="OpenAlex 조회"):
            work = self.get_work_by_doi(doi)
            if not work:
                continue

            # OpenAlex ID (예: "https://openalex.org/W12345" → "W12345")
            oa_id = work.get('id', '').split('/')[-1]
            if oa_id:
                doi_to_oa_id[doi] = oa_id
                oa_id_to_doi[oa_id] = doi

            # referenced_works: 이 논문이 인용한 논문들의 OpenAlex ID URL 리스트
            doi_to_referenced[doi] = [
                ref_url.split('/')[-1]
                for ref_url in work.get('referenced_works', [])
            ]

            # OpenAlex에서 피인용 수 보강
            all_papers[doi]['citation_count'] = work.get('cited_by_count', 0)

            # OpenAlex에서 초록 보강 (Zotero에 없는 경우)
            if not all_papers[doi].get('abstract'):
                abstract_idx = work.get('abstract_inverted_index')
                if abstract_idx:
                    all_papers[doi]['abstract'] = self._reconstruct_abstract(abstract_idx)

        self._save_cache()

        # Step 2: OpenAlex ID 기반으로 인용 엣지 생성
        cites: dict[str, list[str]] = {doi: [] for doi in all_papers}
        cited_by: dict[str, list[str]] = {doi: [] for doi in all_papers}

        for source_doi, referenced_ids in doi_to_referenced.items():
            for ref_oa_id in referenced_ids:
                target_doi = oa_id_to_doi.get(ref_oa_id)
                if target_doi and target_doi != source_doi and target_doi in all_papers:
                    if target_doi not in cites[source_doi]:
                        cites[source_doi].append(target_doi)
                    if source_doi not in cited_by[target_doi]:
                        cited_by[target_doi].append(source_doi)

        total_edges = sum(len(v) for v in cites.values())
        found = len(doi_to_oa_id)
        print(f"  ✅ OpenAlex 매칭: {found}/{len(all_papers)}개 | 인용 엣지: {total_edges}개")

        return cites, cited_by, all_papers
