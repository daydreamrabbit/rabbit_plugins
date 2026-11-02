"""User-managed symmetric relations, stored independently of provider metadata."""
import hashlib
import json

INVERSE = {'main_story':'side_story', 'side_story':'main_story',
           'prequel':'sequel', 'sequel':'prequel', 'spin_off':'main_story',
           'adaptation':'original', 'original':'adaptation',
           'alternative':'alternative', 'other':'other'}

PREFIX = 'rabbit_plugins:related-work:v1:'


def identity(row):
    return [int(row['library_id']), str(row['series_name'])]


def edges(gateway, disabled=False):
    for row in gateway.fetch_all('SELECT `key`, `value` FROM settings WHERE `key` LIKE ?', (PREFIX+'%',)):
        try:
            value=json.loads(row.get('value') or '{}')
            if isinstance(value,dict) and len(value.get('nodes',[]))==2 and bool(value.get('disabled')) == disabled:
                yield row['key'],value['nodes'],value.get('types', ['other', 'other'])
        except (TypeError,ValueError):
            continue


def connected_series(gateway):
    """All series already participating in a saved manual relationship."""
    nodes = set()
    for _, pair, _ in edges(gateway):
        for node in pair:
            if isinstance(node, list) and len(node) == 2:
                try:
                    nodes.add((int(node[0]), str(node[1])))
                except (TypeError, ValueError):
                    continue
    return sorted(nodes)


def typed_links(gateway, row):
    own=identity(row)
    result=[]
    for key,nodes,types in edges(gateway):
        if own not in nodes: continue
        index=nodes.index(own)
        kind=types[index] if isinstance(types,list) and len(types)==2 else 'other'
        result.append((key,nodes[1-index],kind if kind in INVERSE else 'other'))
    return result


def linked(gateway, row):
    return [(key,node) for key,node,kind in typed_links(gateway,row)]


def change(gateway, source, target, remove=False, relation_type="other", reverse_relation_type=None):
    if relation_type not in INVERSE:
        return {'success':False,'error':'지원하지 않는 관계 유형입니다.'}
    reverse = INVERSE[relation_type] if reverse_relation_type is None else reverse_relation_type
    if reverse not in INVERSE:
        return {'success':False,'error':'지원하지 않는 반대쪽 관계 유형입니다.'}
    a,b=identity(source),identity(target)
    if a==b:
        return {'success':False,'error':'같은 시리즈에는 연결할 수 없습니다.'}
    nodes=sorted([a,b])
    key=PREFIX+hashlib.sha256(json.dumps(nodes,ensure_ascii=False).encode()).hexdigest()
    types = [relation_type, reverse] if nodes[0] == a else [reverse, relation_type]
    gateway.set_setting(key,json.dumps({'nodes':nodes, 'types':types, 'disabled': bool(remove)},ensure_ascii=False))
    return {'success':True,'message':'관련작품 연결을 해제했습니다.' if remove else '관련작품을 연결했습니다.'}


def cards(gateway, row, permission='', libraries=(), visible=lambda r:True, check_rating=None, db_type='general'):
    from services.book_service import get_cover_image_with_t
    result=[]
    own = identity(row)
    reverse_types = {}
    for key,nodes,types in edges(gateway):
        if own in nodes and isinstance(types,list) and len(types)==2:
            reverse_types[key] = types[1-nodes.index(own)]
    for key,(library_id,series),kind in typed_links(gateway,row):
        candidates=gateway.fetch_all(
            'SELECT b.id,b.series_name,b.library_id,b.cover_image,b.cover_updated_at,b.author,b.publisher,b.books_lv,b.genre,b.tags '
            'FROM books b WHERE b.library_id=? AND b.series_name=? AND COALESCE(b.is_deleted,0)=0'+permission+
            ' ORDER BY b.id', (library_id,series,*libraries))
        allowed=[r for r in candidates if visible(r) and (not check_rating or check_rating(db_type,r['id']))]
        if not allowed:continue
        representative=next((r for r in allowed if r.get('cover_image')),allowed[0])
        result.append({'book_id':representative['id'],'series_name':series,'display_name':series,
                       'library_id':library_id,'cover':get_cover_image_with_t(representative.get('cover_image'),representative.get('cover_updated_at')),
                       'author':representative.get('author',''),'publisher':representative.get('publisher',''),
                       'book_count':len(allowed), 'relation_type':kind, 'reverse_relation_type':reverse_types.get(key, 'other')})
    return result


def suppressed(gateway, row):
    own = identity(row)
    return {tuple(nodes[1-nodes.index(own)]) for _, nodes, _ in edges(gateway, disabled=True) if own in nodes}


def merge_result(result, linked, blocked=()):
    result = dict(result)
    keys = {(item['library_id'], item['series_name']) for item in linked} | set(blocked)
    relations = {}
    for kind, items in result.get('relations', {}).items():
        relations[kind] = [dict(item, relation_type=kind,
                                reverse_relation_type=INVERSE.get(kind, 'other'), connection_origin='automatic')
                           for item in items if (item.get('library_id'), item.get('series_name')) not in keys]
    for item in linked:
        relations.setdefault(item['relation_type'], []).append(dict(item, connection_origin='manual'))
    result['relations'] = {kind: items for kind, items in relations.items() if items}
    result['recommendations'] = [item for item in result.get('recommendations', [])
                                 if (item.get('library_id'), item.get('series_name')) not in keys]
    return result
