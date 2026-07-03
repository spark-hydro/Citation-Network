"""
Citation Network Builder
========================
python main.py                             → 컬렉션 선택 화면 (트리)
python main.py --collection "컬렉션 이름"  → 바로 실행 (하위 폴더 포함)
python main.py --test                      → API 연결 테스트
"""
import os
import sys
import argparse
from dotenv import load_dotenv

load_dotenv()

# ── 설정 (.env에서 로드) ──────────────────────────────────────────────
ZOTERO_USER_ID  = os.getenv('ZOTERO_USER_ID', '')
ZOTERO_API_KEY  = os.getenv('ZOTERO_API_KEY', '')
OBSIDIAN_VAULT  = os.getenv('OBSIDIAN_VAULT_PATH', r'C:\Users\User\OneDrive\Obsidian')
OUTPUT_FOLDER   = os.getenv('CITATION_NETWORK_FOLDER', '13. Citation Network')
OPENALEX_EMAIL  = os.getenv('OPENALEX_EMAIL', '')
CACHE_FILE      = os.path.join(os.path.dirname(__file__), 'cache', 'openalex_cache.json')

sys.path.insert(0, os.path.dirname(__file__))
from src.zotero_client   import ZoteroClient
from src.openalex_client import OpenAlexClient
from src.obsidian_writer import ObsidianWriter


# ────────────────────────────────────────────────────────────────────
def _print_tree(tree: dict, keys: list, prefix: str, numbered: list) -> None:
    """트리를 재귀적으로 출력하고 numbered 리스트에 순서대로 key를 추가"""
    for i, key in enumerate(keys):
        col       = tree[key]
        is_last   = (i == len(keys) - 1)
        connector = '└── ' if is_last else '├── '
        child_pre = '    ' if is_last else '│   '

        has_children = bool(col['children'])
        marker = '📁' if has_children else '📄'

        num = len(numbered) + 1
        numbered.append(key)
        print(f"  {prefix}{connector}{num:3d}. {marker} {col['name']}")

        if has_children:
            _print_tree(tree, col['children'], prefix + child_pre, numbered)


def pick_collection(zotero: ZoteroClient) -> tuple[str, bool]:
    """
    트리 구조로 컬렉션을 선택합니다.

    Returns:
        (collection_key, is_subtree)
        is_subtree=True → 하위 폴더 포함 재귀 수집
    """
    print("\n📋 Zotero 컬렉션 트리 불러오는 중...")
    tree, roots = zotero.get_collection_tree()

    numbered: list[str] = []   # 번호 순서대로 key 저장

    print("\n" + "=" * 60)
    print("  조테로 폴더를 선택하세요")
    print("  📁 = 하위 폴더 있음 (선택 시 하위 폴더 논문 모두 포함)")
    print("  📄 = 단일 폴더")
    print("=" * 60)

    _print_tree(tree, roots, '', numbered)

    print("─" * 60)
    print(f"   all.  전체 처리 ({len(tree)}개 컬렉션)")
    print("=" * 60)

    while True:
        try:
            choice = input("\n번호 입력 → ").strip()

            if choice.lower() == 'all':
                print("  → 전체 컬렉션 처리")
                return '', False

            idx = int(choice) - 1
            if 0 <= idx < len(numbered):
                key  = numbered[idx]
                name = tree[key]['name']
                has_children = bool(tree[key]['children'])
                if has_children:
                    print(f"  → '{name}' + 하위 폴더 모두 처리\n")
                else:
                    print(f"  → '{name}' 처리\n")
                return key, True   # 항상 subtree 방식 사용
            print(f"  ⚠️  1~{len(numbered)} 사이의 번호를 입력하세요.")

        except ValueError:
            print("  ⚠️  숫자 또는 'all'을 입력하세요.")
        except KeyboardInterrupt:
            print("\n\n취소됨.")
            sys.exit(0)


# ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--collection', type=str, default=None,
                        help='컬렉션 이름 (하위 폴더 포함 자동 처리)')
    parser.add_argument('--test', action='store_true')
    args, _ = parser.parse_known_args()

    print("=" * 60)
    print("  📚 Citation Network Builder")
    print("=" * 60)

    if not ZOTERO_USER_ID or not ZOTERO_API_KEY:
        print("❌ .env 파일에 ZOTERO_USER_ID와 ZOTERO_API_KEY를 설정하세요.")
        sys.exit(1)

    zotero   = ZoteroClient(ZOTERO_USER_ID, ZOTERO_API_KEY)
    openalex = OpenAlexClient(email=OPENALEX_EMAIL, cache_file=CACHE_FILE)
    writer   = ObsidianWriter(OBSIDIAN_VAULT, OUTPUT_FOLDER)

    # ── 테스트 모드 ──────────────────────────────────────────────────
    if args.test:
        print("\n🔧 API 연결 테스트 중...\n")
        cols = zotero.get_collections()
        print(f"  ✅ Zotero:    {len(cols)}개 컬렉션 연결 성공")
        work = openalex.get_work_by_doi('10.1093/aje/kwu178')
        print(f"  {'✅' if work else '❌'} OpenAlex:  {'연결 성공' if work else '연결 실패'}")
        print("\n테스트 완료.")
        return

    # ── 컬렉션 선택 ──────────────────────────────────────────────────
    if args.collection is not None:
        # --collection 이름으로 key 찾기
        tree, roots = zotero.get_collection_tree()
        matched = [k for k, v in tree.items() if v['name'] == args.collection]
        if not matched:
            print(f"❌ '{args.collection}' 컬렉션을 찾을 수 없습니다.")
            sys.exit(1)
        collection_key = matched[0]
        print(f"\n  → '{args.collection}' 컬렉션 사용 (하위 폴더 포함)\n")
    else:
        tree, _ = None, None
        collection_key, _ = pick_collection(zotero)
        tree, _ = zotero.get_collection_tree()

    # ── Step 1: 논문 수집 (subtree 재귀) ─────────────────────────────
    print("📚 논문 목록 불러오는 중...")

    if collection_key == '':
        # 전체 처리
        papers = zotero.get_all_collections_with_papers()
    else:
        # 선택한 폴더 + 모든 하위 폴더 재귀
        papers = zotero.get_papers_in_subtree(collection_key, tree)

    if not papers:
        print("❌ 해당 컬렉션에 논문이 없습니다.")
        sys.exit(1)

    total = sum(len(v['papers']) for v in papers.values())
    no_doi = sum(1 for v in papers.values() for p in v['papers'] if not p.get('doi'))
    print(f"✅ {len(papers)}개 컬렉션, {total}개 논문 발견")
    if no_doi:
        print(f"  ⚠️  DOI 없는 논문 {no_doi}개는 인용 관계 조회에서 제외됩니다.")

    # ── Step 2: OpenAlex 인용 관계 ────────────────────────────────────
    cites, cited_by, all_papers = openalex.build_citation_network(papers)

    edges  = sum(len(v) for v in cites.values())
    linked = sum(1 for doi in all_papers if cites.get(doi) or cited_by.get(doi))
    print(f"\n🔗 인용 엣지: {edges}개  |  인용 관계 있는 논문: {linked}/{total}개")

    # ── Step 3: Obsidian 노트 생성 ────────────────────────────────────
    print(f"\n📝 Obsidian 노트 생성 중...\n")
    writer.write_all(papers, cites, cited_by, all_papers)

    print(f"\n🎉 완료! Obsidian에서 Graph View를 열어 확인하세요.")
    print(f"   폴더: {OUTPUT_FOLDER}")


if __name__ == '__main__':
    main()
