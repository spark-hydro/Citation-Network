"""
src/zotero_client.py
Zotero Web API에서 컬렉션 및 논문 메타데이터를 가져옵니다.
"""
import re
from pyzotero import zotero
from tqdm import tqdm


class ZoteroClient:
    """Zotero API 클라이언트"""

    PAPER_TYPES = {
        'journalArticle', 'conferencePaper', 'book',
        'bookSection', 'preprint', 'thesis', 'report', 'manuscript',
    }

    def __init__(self, library_id: str, api_key: str, library_type: str = 'user'):
        self.zot = zotero.Zotero(library_id, library_type, api_key)

    # ------------------------------------------------------------------ #
    # 컬렉션
    # ------------------------------------------------------------------ #
    def get_collections(self) -> list[dict]:
        """모든 컬렉션 반환: [{key, name, parent_key}]"""
        raw = self.zot.collections()
        return [
            {
                'key': c['key'],
                'name': c['data']['name'],
                'parent_key': c['data'].get('parentCollection') or None,
            }
            for c in raw
        ]

    def get_collection_tree(self) -> tuple[dict, list]:
        """
        컬렉션 트리 구조를 반환합니다.

        Returns:
            tree  : {key: {name, parent_key, children: [key, ...]}}
            roots : 최상위 컬렉션 key 리스트 (부모 없는 것)
        """
        cols = self.get_collections()
        tree = {c['key']: {**c, 'children': []} for c in cols}

        roots = []
        for key, col in tree.items():
            pk = col['parent_key']
            if pk and pk in tree:
                tree[pk]['children'].append(key)
            else:
                roots.append(key)

        # 이름순 정렬
        roots.sort(key=lambda k: tree[k]['name'])
        for col in tree.values():
            col['children'].sort(key=lambda k: tree[k]['name'])

        return tree, roots

    def get_papers_in_subtree(self, collection_key: str, tree: dict) -> dict:
        """
        선택한 컬렉션과 모든 하위 컬렉션의 논문을 재귀 수집합니다.

        Returns:
            {col_name: {key, parent_key, papers: [...]}, ...}
        """
        result = {}
        col = tree[collection_key]

        # 현재 컬렉션 논문
        papers = self.get_items_in_collection(collection_key)
        if papers:
            result[col['name']] = {
                'key': collection_key,
                'parent_key': col['parent_key'],
                'papers': papers,
            }

        # 하위 컬렉션 재귀
        for child_key in col['children']:
            result.update(self.get_papers_in_subtree(child_key, tree))

        return result

    # ------------------------------------------------------------------ #
    # 항목 수집
    # ------------------------------------------------------------------ #
    def get_items_in_collection(self, collection_key: str) -> list[dict]:
        """컬렉션 내 논문 항목의 메타데이터 리스트 반환"""
        raw_items = self.zot.everything(self.zot.collection_items(collection_key))
        papers = []
        for item in raw_items:
            if item['data'].get('itemType') in self.PAPER_TYPES:
                meta = self._extract_metadata(item)
                if meta:
                    papers.append(meta)
        return papers

    def get_all_collections_with_papers(self, progress: bool = True, key_filter: str = '') -> dict:
        """
        모든 컬렉션(또는 특정 key의 컬렉션)과 논문을 반환.
        key_filter: 컬렉션 key ('' 이면 전체)
        """
        collections = self.get_collections()

        if key_filter:
            collections = [c for c in collections if c['key'] == key_filter]
            if not collections:
                return {}

        result = {}
        iterator = tqdm(collections, desc="Fetching collections") if progress else collections
        for col in iterator:

            papers = self.get_items_in_collection(col['key'])
            if papers:
                result[col['name']] = {
                    'key': col['key'],
                    'parent_key': col['parent_key'],
                    'papers': papers,
                }

        return result

    # ------------------------------------------------------------------ #
    # 메타데이터 추출
    # ------------------------------------------------------------------ #
    def _extract_metadata(self, item: dict) -> dict | None:
        data = item['data']

        title = data.get('title', '').strip()
        if not title:
            return None

        # DOI 정규화
        doi = data.get('DOI', '').strip().lower()
        doi = doi.replace('https://doi.org/', '').replace('http://doi.org/', '').strip('/')

        # citekey (Better BibTeX Extra 필드)
        citekey = self._parse_citekey(data.get('extra', ''))

        # citekey 없으면 자동 생성
        if not citekey:
            citekey = self._generate_citekey(data)

        # 저자
        creators = data.get('creators', [])
        authors = []
        for c in creators:
            if c.get('creatorType') in ('author', 'editor'):
                last = c.get('lastName', '').strip()
                first = c.get('firstName', '').strip()
                name = f"{last}, {first}".strip(', ')
                if name:
                    authors.append(name)

        # 연도
        date_str = data.get('date', '')
        year = re.search(r'\d{4}', date_str)
        year = year.group(0) if year else ''

        # 저널/출판사
        journal = (
            data.get('publicationTitle')
            or data.get('bookTitle')
            or data.get('publisher')
            or ''
        ).strip()

        return {
            'zotero_key': item['key'],
            'title': title,
            'authors': authors,
            'year': year,
            'journal': journal,
            'doi': doi,
            'citekey': citekey,
            'abstract': data.get('abstractNote', '').strip(),
            'tags': [t['tag'] for t in data.get('tags', [])],
            'item_type': data.get('itemType', ''),
            'volume': data.get('volume', ''),
            'issue': data.get('issue', ''),
            'pages': data.get('pages', ''),
        }

    @staticmethod
    def _parse_citekey(extra: str) -> str | None:
        """Extra 필드에서 Better BibTeX Citation Key 파싱"""
        for line in extra.splitlines():
            line = line.strip()
            lower = line.lower()
            if lower.startswith('citation key:'):
                return line.split(':', 1)[1].strip()
            if lower.startswith('citekey:'):
                return line.split(':', 1)[1].strip()
        return None

    @staticmethod
    def _generate_citekey(data: dict) -> str:
        """citekey가 없을 때 자동 생성: lastname_year_journalabbrev"""
        creators = data.get('creators', [])
        last_name = ''
        for c in creators:
            if c.get('creatorType') == 'author':
                last_name = re.sub(r'[^a-zA-Z]', '', c.get('lastName', '')).lower()
                break

        date_str = data.get('date', '')
        year_match = re.search(r'\d{4}', date_str)
        year = year_match.group(0) if year_match else 'xxxx'

        journal = re.sub(r'[^a-zA-Z]', '', data.get('publicationTitle', ''))[:8].lower()

        parts = [p for p in [last_name, year, journal] if p]
        return '_'.join(parts) if parts else 'unknown'
