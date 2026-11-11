const fs = require('fs');
const vm = require('vm');
const path = require('path');
const assert = require('assert');
const source = fs.readFileSync(path.join(__dirname, '../detail/script.js'), 'utf8');
{
  const visibility=source.slice(source.indexOf('  function relationGraphVisible('),source.indexOf('  function refreshRelationGraphDisplay('));
  const scope={};vm.createContext(scope);vm.runInContext(visibility,scope);
  assert.equal(scope.relationGraphVisible(null,false),false,'do not start the graph before saved settings arrive');
  for(const enabled of [false,true])for(const mobileEnabled of [false,true])for(const mobile of [false,true]) {
    assert.equal(scope.relationGraphVisible({enabled,mobileEnabled},mobile),enabled&&(!mobile||mobileEnabled),'master and mobile preferences combine independently');
  }
}
const layout = source.slice(source.indexOf('  function relationGraphLayout('), source.indexOf('  function loadRelationGraphEngine('));
const topology = source.slice(source.indexOf('  function relationGraphTopology('), source.indexOf('  async function renderRelationGraph('));
const context = { relationLabels: { prequel: '전작', sequel: '후속작', side_story: '외전', spin_off: '스핀오프', main_story: '본편' } };
const viewportSource=source.slice(source.indexOf('  function relationGraphViewport('),source.indexOf('  function loadRelationGraphEngine('));
vm.createContext(context); vm.runInContext(layout + topology, context);
for(const width of [240,320,600,1000]) {
  const rec=context.relationGraphRecommendationLayout(11,width),boxes=[];
  rec.positions.forEach(([x,y],i)=>{
    const b={left:x-rec.labelWidth/2,right:x+rec.labelWidth/2,top:y-30,bottom:y+(i?100:68)};
    assert.ok(!boxes.some(p=>b.left<p.right && b.right>p.left && b.top<p.bottom && b.bottom>p.top),'recommendation cover/title envelopes do not overlap at width '+width);
    boxes.push(b);
  });
}
assert.equal(context.relationGraphUpdateCaption({current:true,captionRecipe:{above:true,x:98}},[],{width:300,height:300},true).above,false,'current badge always stays below the cover');
{
  const nodes=[{id:'2',kind:'전작',label:'제2장'},{id:'4',kind:'후속작',label:'제4장'},{id:'side',label:'검귀연가'}];
  const links=[{source:'side',target:'2',kind:'main_story',label:'본편'},{source:'side',target:'4',kind:'main_story',label:'본편'}];
  assert.equal(context.relationGraphNodeRelations(nodes[0],links,nodes).length,0,'ordinary captions only describe the focused work relationship');
  assert.equal(context.relationGraphPeerDescriptions(nodes[0],links,nodes)[0],'검귀연가 기준: 본편','peer roles retain their actual source context in the preview');
  const books=Array.from({length:30},(_,i)=>({library_id:1,series_name:String(i)}));
  const sample=context.relationGraphRecommendationSample([...books,books[0]],()=>0);
  assert.equal(sample.length,10);assert.equal(new Set(sample.map(b=>b.series_name)).size,10);
  assert.equal(books[0].series_name,'0','sampling does not mutate response order');
  assert.equal(context.relationGraphRecommendationSample([]).length,0);
  const prioritized=[...Array.from({length:3},(_,i)=>({library_id:1,series_name:'mu'+i,recommendation_source:'manga_updates'})),
    ...Array.from({length:12},(_,i)=>({library_id:1,series_name:'similar'+i,recommendation_source:'similarity',score:20-i})),
    {library_id:1,series_name:'fallback',recommendation_source:'genre_tag_fallback'}];
  const rankedSample=context.relationGraphRecommendationSample(prioritized,()=>0);
  assert.equal(rankedSample.length,10);
  assert.ok(rankedSample.slice(0,3).every(book=>book.recommendation_source==='manga_updates'),
    'curated MangaUpdates picks remain ahead of library matches');
  assert.deepEqual(rankedSample.slice(3).map(book=>book.score),[20,19,18,17,16,15,14],
    'library matches keep their author/tag relevance score order');
  const broadFallback=context.relationGraphRecommendationSample(Array.from({length:15},(_,i)=>({
    library_id:1,series_name:'fallback'+i,recommendation_source:'genre_tag_fallback'})),()=>0);
  assert.equal(broadFallback.length,10);
  assert.ok(broadFallback.every(book=>book.recommendation_source==='genre_tag_fallback'));
  const tones=Array.from({length:100},(_,i)=>context.relationGraphRecommendationTone({
    series_name:'추천 작품 '+i,recommendation_source:'similarity'}));
  assert.deepEqual([...new Set(tones)].sort(),[0,1,2,3],
    'recommendation nodes use all four theme tones deterministically');
  assert.equal(context.relationGraphRecommendationTone({series_name:'Alpha',recommendation_source:'similarity'}),
    context.relationGraphRecommendationTone({series_name:'Alpha',recommendation_source:'similarity'}),
    'the same recommendation keeps its tone across renders');
}
assert.equal(context.relationGraphNodeRelations({id:'manga',kind:'각색'},[
  {target:'manga',kind:'main_story',label:'본편',showLabel:false},
  {target:'manga',kind:'main_story',label:'본편'},
  {target:'manga',kind:'adaptation',label:'각색'},
  {target:'other',kind:'side_story',label:'외전'}]).join(','),'본편','node captions include registered incoming roles once, without repeating the main classification');
vm.runInContext(viewportSource,context);
for(const width of [240,330,440,760,1080,1440,1920]) {
  const viewport=context.relationGraphViewport(width,4);
  assert.ok(Math.abs(viewport.width*viewport.scale-width)<1e-8,'scaled content exactly matches available width');
  assert.ok(viewport.scale>=1 && viewport.scale<=1.6,'covers and text stay within readable scale limits');
}
assert.equal(context.relationGraphViewport(330,4).scale,1,'small screens retain readable original size');
assert.ok(context.relationGraphViewport(1080,4).scale>1 && context.relationGraphViewport(1080,4).scale<=1.25,'sparse wide graphs enlarge modestly without oversized covers');
assert.ok(context.relationGraphViewport(1080,14,true).scale<=1.1,'grouped graphs keep a compact scale');
assert.ok(context.relationGraphViewport(1440,24).scale<context.relationGraphViewport(1440,4).scale,'dense graphs enlarge less to preserve room');
for(const width of [820,1074,1230,1640])assert.equal(Math.floor(context.relationGraphViewport(width,14,true).width/410),Math.floor(width/410),'enlarging grouped graphs preserves available columns');
const works = Array.from({length: 13}, (_, i) => ({book: {
  library_id: 2, series_name: i < 6 ? '본편 공식 앤솔로지：' + i : '다른 작품 ' + i,
}}));
const edges = [...Array.from({length: 5}, (_, i) => ({
  source_key: ['2', works[i].book.series_name], target_key: ['2', works[i+1].book.series_name], relation_type: 'sequel',
})), {source_key: ['2', '다른 작품 6'], target_key: ['2', '다른 작품 7'], relation_type: 'sequel'},
{source_key: ['99', '숨겨진 작품'], target_key: ['2', '다른 작품 8'], relation_type: 'sequel'}];
const result = context.relationGraphTopology(works, {edges}, '본편');
assert.equal(result.groups.length, 2);
assert.equal(result.groups[0].members.join(','), '0,1,2,3,4,5');
assert.equal(result.groups[0].label, '공식 앤솔로지');
assert.equal(result.groups[1].members.join(','), '6,7');
assert.equal(result.extraEdges.length, 6);
assert.equal(result.extraEdges[0].label,'후속작');
assert.equal(context.relationGraphTopology(works,{edges:[dictEdge('prequel')]},'본편').extraEdges[0].label,'전작');
assert.equal(context.relationGraphTopology(works, {edges: []}, '본편').groups.length, 0);
const sideStory = context.relationGraphTopology(works, {edges: [dictEdge('side_story')]}, '본편');
assert.equal(sideStory.groups.length, 0);
assert.equal(sideStory.extraEdges[0].label, '외전');
function dictEdge(kind) { return {source_key: ['2', works[0].book.series_name], target_key: ['2', works[1].book.series_name], relation_type: kind}; }
const cycle = context.relationGraphTopology(works, {edges: [dictEdge('sequel'), {
  source_key: ['2', works[1].book.series_name], target_key: ['2', works[0].book.series_name], relation_type: 'sequel',
}]}, '본편');
assert.equal(cycle.groups[0].members.length, 2);
assert.equal(cycle.groups[0].sequence, false, 'a cycle has no proven first work');
const seriesOnly = context.relationGraphTopology(works, {edges:[dictEdge('series')]}, '본편');
assert.equal(seriesOnly.groups[0].sequence, false, 'series membership alone does not imply reading order');
assert.equal(result.groups[0].sequence, true);
const nodes = [{id: 'current', current: true}, ...works.map((_, i) => ({id: String(i)}))];
for (const group of result.groups) {
  for (const id of group.members) nodes.find(n => n.id === id).groupId = group.id;
  nodes.push({id: group.id, group: true});
}
for (const width of [240, 320, 390, 640, 967, 1280]) {
  const layout = context.relationGraphGroupedLayout(nodes, width, result.groups);
  const boxes = [];
  nodes.forEach((n, i) => {
    if (n.group) return;
    const group = result.groups.find(group => group.id === n.groupId);
    const [x, y] = layout.positions[i], w = group?.members.length === 2
      ? Math.min(112, layout.groupBoxes.get(group.id).width - 32) : n.groupId ? 80 : 112;
    const box = {left: x-w/2, right: x+w/2, top: y-(n.current?30:24), bottom: y+(n.current?87:81)};
    assert.ok(box.left >= -width/2 && box.right <= width/2 && box.top >= -layout.height/2 && box.bottom <= layout.height/2, 'clipped at ' + width);
    assert.ok(!boxes.some(b => box.left < b.right && box.right > b.left && box.top < b.bottom && box.bottom > b.top), 'overlap at ' + width);
    boxes.push(box);
  });
}
console.log('PASS explicit groups, order, hidden edges, non-sequence links, cycles, and six responsive grouped layouts');
{
  const entries=Array.from({length:5},(_,i)=>({book:{library_id:i<2?13:2,series_name:'Goblin '+i},type:i<2?'prequel':'adaptation'}));
  const sourceEdges=[{source_key:['2','Goblin 2'],target_key:['2','Goblin 3'],relation_type:'side_story'},
    {source_key:['2','Goblin 3'],target_key:['2','Goblin 2'],relation_type:'main_story'},
    {source_key:['2','Goblin 2'],target_key:['2','Goblin 4'],relation_type:'side_story'}];
  const topology=context.relationGraphTopology(entries,{edges:sourceEdges},'Goblin');
  assert.equal(topology.groups.length,1);
  assert.equal(topology.groups[0].category,'adaptation');
  assert.equal(topology.groups[0].label,'각색 작품');
  assert.equal(topology.groups[0].members.join(','),'2,3,4');
  assert.equal(topology.groups[0].sequence,false,'adaptation category never invents a reading order');
  assert.equal(topology.extraEdges.length,3,'category grouping adds no inferred edges');
  const labeledNodes=[{id:'current',current:true},...entries.map((entry,i)=>({id:String(i),label:entry.book.series_name,kind:i<2?'전작':'각색',groupId:i>=2?'group-0':undefined})),{id:'group-0',group:true}];
  const labeledLinks=context.relationGraphLinks(labeledNodes,topology);
  const reciprocal=labeledLinks.find(link=>link.source==='2' && link.target==='3');
  assert.equal(reciprocal.labelRoles,null,'internal group links use compact relation names without repeating book titles');
  assert.equal(reciprocal.label,'외전');
  assert.equal(labeledLinks.find(link=>link.source==='3' && link.target==='2').label,'본편','explicit reverse relationship is preserved');
  const outsideLinks=context.relationGraphLinks(labeledNodes.map(n=>({...n,groupId:undefined})),{groups:[],extraEdges:topology.extraEdges});
  assert.equal(outsideLinks.find(link=>link.source==='2' && link.target==='4').labelRoles[0].role,'기준','missing reverse relationship does not invent a main-story role');
  const single=context.relationGraphTopology([entries[2]],{edges:[]},'Goblin');
  assert.equal(single.groups[0].category,'adaptation','one adaptation also gets its own visual category');
  assert.equal(single.groups[0].sequence,false,'a visual category implies no reading order');
  const fromManga=[
    {book:{library_id:13,series_name:'Novel'},type:'original'},
    {book:{library_id:13,series_name:'Before'},type:'prequel'},
    {book:{library_id:2,series_name:'Day'},type:'side_story'},
    {book:{library_id:2,series_name:'Brand'},type:'side_story'}];
  const explicitAdaptations=[{source_key:['13','Novel'],target_key:['2','Day'],relation_type:'adaptation'},
    {source_key:['13','Novel'],target_key:['2','Brand'],relation_type:'adaptation'}];
  const mangaTopology=context.relationGraphTopology(fromManga,{edges:explicitAdaptations},'Manga');
  assert.equal(mangaTopology.groups[0].members.join(','),'current,1,2,3','the focused adaptation and its registered prequel/side stories share a family');
  assert.equal(mangaTopology.groups[0].category,'focused-family');
  assert.equal(mangaTopology.groups.some(group=>group.category==='adaptation'),false,'peer adaptation edges do not override direct side-story classifications');
  assert.equal(mangaTopology.extraEdges.length,2,'view direction adds no inferred relationships');
  const familyNodes=[{id:'current',current:true,groupId:'group-current-family'},...fromManga.map((work,i)=>({id:String(i),relationType:work.type,groupId:i?'group-current-family':undefined})),{id:'group-current-family',group:true}];
  const familyLinks=context.relationGraphLinks(familyNodes,mangaTopology);
  assert.equal(familyLinks.filter(link=>link.spoke).length,4,'focus connects to original and all three directly registered peers');
  assert.equal(familyLinks.some(link=>link.target==='group-current-family'),false,'focus never connects to its own membrane');
  assert.equal(familyLinks.some(link=>link.kind==='adaptation'),false,'cross-family source details do not obscure the focus relationships');
  for(const width of [330,760,1080]) {
    const placed=context.relationGraphGroupedLayout(familyNodes,width,mangaTopology.groups).positions;
    assert.ok(placed[2][0]<placed[0][0],'prequel remains left of focus inside its family');
    const peers=placed.slice(2,5),focus=placed[0];
    assert.ok(peers.some(p=>p[0]>focus[0]) && peers.some(p=>p[0]<focus[0]),'related works surround the initial focus on both sides');
    assert.ok(peers.some(p=>p[1]>focus[1]) && peers.some(p=>p[1]<focus[1]),'related works surround the initial focus above and below');
    assert.ok(Math.hypot(...peers.reduce((sum,p)=>[sum[0]+p[0]-focus[0],sum[1]+p[1]-focus[1]],[0,0]))<1e-8,'focus starts at the peer centroid');
    assert.ok(placed.every(p=>p.every(Number.isFinite)));
  }
  for(const width of [330,760]) {
    const nodes=[{id:'current',current:true},{id:'original',relationType:'original'},{id:'before',relationType:'prequel'}];
    const placed=context.relationGraphBranchLayout(nodes,width,[],width<480).positions;
    assert.ok(placed[2][0]<placed[0][0],'prequel stays left of manga focus');
    assert.ok(placed[1][0]<placed[0][0] || placed[1][1]<placed[0][1],'original has its own upstream route away from current cover');
  }
  for(const width of [330,760,1080]) {
    const graphNodes=[{id:'current',current:true},...entries.map((e,i)=>({id:String(i),relationType:e.type,groupId:i>=2?'group-0':undefined})),{id:'group-0',group:true}];
    const placed=context.relationGraphGroupedLayout(graphNodes,width,topology.groups).positions;
    assert.ok(placed[1][0]<placed[0][0] && placed[2][0]<placed[0][0],'source prequels remain left of the current work outside adaptation category');
  }
}
{
  const pairNodes=[{id:'current',current:true},{id:'a',groupId:'g'},{id:'b',groupId:'g'},{id:'g',group:true}];
  const pairGroup=[{id:'g',members:['a','b']}];
  const wide=context.relationGraphGroupedLayout(pairNodes,1400,pairGroup);
  assert.ok(Math.abs(wide.positions[1][0]-wide.positions[2][0])<=220,'two-work groups do not spread across an oversized panel');
  const compact=context.relationGraphGroupedLayout(pairNodes,330,pairGroup);
  assert.ok(Math.abs(compact.positions[1][1]-compact.positions[2][1])>=108,'compact vertical pairs preserve cover and title clearance');
}

const previewNodes = [{id: 'current', current: true}, {id: '0', kind: '외전', groupId: 'g'},
  {id: '1', kind: '외전', groupId: 'g'}, {id: '2', kind: '스핀오프'}, {id: 'g', group: true}];
const routed = context.relationGraphLinks(previewNodes, {groups: [{id: 'g', sequence:true, members:['0','1']}], extraEdges: [
  {source: '0', target: '1', kind: 'sequel', label: '후속작'},
  {source: '1', target: '0', kind: 'side_story', label: '외전'},
]});
assert.equal(routed.length, 4, 'retain each explicit relation plus root spokes');
assert.equal(routed.filter(link => link.groupId === 'g').map(link => link.label).join('|'), '후속작|외전');
assert.equal(routed.find(link => link.kind === 'side_story').source, '1', 'preserve original relation direction');
assert.equal(routed.find(link=>link.kind==='side_story').showLabel,false,'existing target relation badge does not need a duplicate floating label');
const unique = context.relationGraphLinks(previewNodes, {groups: [{id:'g', sequence:true, members:['0','1']}], extraEdges: [
  {source: '0', target: '1', kind: 'sequel', label: '후속작'},
  {source: '1', target: '0', kind: 'main_story', label: '본편'},
]});
assert.equal(unique.find(link => link.kind === 'main_story').label, '본편', 'distinct relationship belongs on the connector between works');
assert.equal(unique.find(link => link.groupId === 'g').captionLabel, undefined, 'relationship must not describe the whole group');
const groupBox = {x: 0, y: 0, groupWidth: 340, groupHeight: 180, members:['0','1']};
const curve = context.relationGraphCurve({id:'0', x:-100, y:0}, {id:'1', x:100, y:0}, groupBox);
assert.equal(curve.point(.5).y, 0, 'pair relation belongs between the two covers');
assert.ok(curve.sx > -100 && curve.ex < 100, 'pair connectors face the central relationship caption');
const sameCurve = context.relationGraphCurve({id:'8', x:-100, y:0}, {id:'9', x:100, y:0}, groupBox);
assert.equal(curve.point(.5).y, sameCurve.point(.5).y, 'routing does not depend on odd/even node IDs');
for (let t=0; t<=1; t+=.05) {
  const p=curve.point(t); assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y));
  assert.ok(Math.abs(p.x)<170 && Math.abs(p.y)<90, 'pair route fits its group');
}
console.log('PASS merged connectors, duplicate label suppression, retained distinct labels, and consistent outer routing');
const pairLinks = unique.filter(link => link.groupId === 'g');
const positions = {'0':{id:'0',x:-100,y:0}, '1':{id:'1',x:100,y:0}};
const mids = pairLinks.map(link => context.relationGraphCurve(positions[link.source], positions[link.target], groupBox, link).point(.5));
assert.equal(mids[0].y, mids[1].y, 'opposite directions share the same connection');
const endpointMarks=pairLinks.map(link=>context.relationGraphEndMark(context.relationGraphCurve(positions[link.source],positions[link.target],groupBox,link),42));
assert.ok(endpointMarks[0].x>0 && endpointMarks[1].x<0,'each relation label stays by its own target end');
const baseBounds = {x:0,y:0,width:340,height:200};
for (const point of [{x:260,y:20},{x:-220,y:-180},{x:40,y:240}]) {
  const member = {...point,labelWidth:112,boxHeight:104};
  const bounds = context.relationGraphGroupBounds(baseBounds,[member]);
  assert.ok(bounds.width > 0 && bounds.height > 0 && bounds.path.includes(' Q '));
  for (const x of [point.x-66,point.x+66]) for (const y of [point.y-36,point.y+92]) {
    assert.ok(x >= bounds.x-bounds.width/2 && x <= bounds.x+bounds.width/2 && y >= bounds.y-bounds.height/2 && y <= bounds.y+bounds.height/2, 'contour encloses cover and title');
  }
  assert.deepEqual(context.relationGraphGroupBounds(baseBounds,[member]),bounds,'bounds do not grow cumulatively on each frame');
}
console.log('PASS dynamic group expansion in both axes and stable repeated bounds');
const resting=[{x:-100,y:0,labelWidth:112,boxHeight:104},{x:100,y:0,labelWidth:112,boxHeight:104}];
const compact=context.relationGraphGroupBounds(baseBounds,resting);
const stretched=context.relationGraphGroupBounds(baseBounds,[resting[0],{...resting[1],x:300}]);
assert.equal(stretched.height,compact.height,'horizontal stretch must not inflate vertical size');
assert.equal(stretched.width-compact.width,200,'only the moved side extends');
assert.equal(stretched.x-stretched.width/2,compact.x-compact.width/2,'opposite boundary stays in place');
const multiGroup={x:0,y:0,groupWidth:500,groupHeight:500,members:['0','1','2']};
for (const coords of [[0,0,20,160],[-130,50,-100,200],[0,-5,20,160]]) {
  const [ax,ay,bx,by]=coords;
  const curve=context.relationGraphCurve({id:'0',x:ax,y:ay},{id:'1',x:bx,y:by},multiGroup);
  const dx=curve.ex-curve.sx,dy=curve.ey-curve.sy;
  for(let t=0;t<1;t+=.05){const p=curve.point(t),q=curve.point(t+.01);assert.ok((q.x-p.x)*dx+(q.y-p.y)*dy>0,'connector never doubles back after dragging');}
}
console.log('PASS compact contours, directional stretching, and no connector reversal');
const rootLink=context.relationGraphCurve({id:'current',x:-300,y:0,current:true},
  {id:'g',x:0,y:0,group:true,outline:[{x:-100,y:-80},{x:100,y:-80},{x:100,y:80},{x:-100,y:80}]});
assert.equal(rootLink.ey,0);
assert.ok(rootLink.ex < -100 && rootLink.ex > rootLink.sx,'root connector terminates at the facing group boundary');
const closePair=context.relationGraphCurve({id:'0',x:0,y:0,labelWidth:112,boxHeight:104},
  {id:'1',x:20,y:100,labelWidth:112,boxHeight:104},groupBox,{lane:32});
assert.ok(closePair.len>closePair.ra+closePair.rb,'short vertical connector does not invert');
console.log('PASS cross-group and sibling collision guards, root attachment, and close pair routing');
const detour=context.relationGraphCurve({id:'current',x:-300,y:0,current:true},
  {id:'g',x:0,y:0,group:true,outline:[{x:-100,y:-120},{x:100,y:-120},{x:100,y:120},{x:-100,y:120}]},null,
  {obstacles:[{x:-200,y:0,labelWidth:56,boxHeight:72}]});
assert.ok(Math.abs(detour.ey)>30,'root connector selects a boundary attachment away from intervening work');
const softNode=(id,x,y,groupId)=>({id,x,y,groupId,labelWidth:80,boxHeight:96});
{
  const pair=[softNode('dazai',0,-90,'pair'),softNode('storm',0,130,'pair')];
  const springs=context.relationGraphSoftLinks(pair);
  pair[0].y=-60;
  const before=pair.map(n=>({x:n.x,y:n.y}));
  context.relationGraphCommitPose(pair,springs);
  for(let frame=0;frame<240;frame++)context.relationGraphSoftStep(pair,springs,pair[1],{width:1600,height:1400});
  pair.forEach((n,i)=>assert.ok(Math.hypot(n.x-before[i].x,n.y-before[i].y)<.001,'a dropped diagonal/vertical pair retains its accepted spacing instead of returning to the initial spring length'));
  const recipe={x:0,above:false,top:32,bottom:94};
  const upper={...pair[0],captionRecipe:recipe};
  for(const dx of [-3,0,3]) {
    const neighbours=[{...pair[1],x:dx}];
    assert.equal(context.relationGraphUpdateCaption(upper,neighbours,{width:1600}),recipe,'idle floating cannot flip an accepted title or enlarge the membrane');
  }
  assert.equal(context.relationGraphUpdateCaption(upper,[pair[1]],{width:1600},true).above,false,'a new drag retains a clear accepted caption despite a downward connection');
  assert.equal(context.relationGraphUpdateCaption(upper,[{...pair[1],x:upper.x,y:upper.y+60}],{width:1600},true).above,true,
    'a cover actually occupying the caption still moves it to the free side');
}
const branches=[{id:'current',current:true},{id:'0',relationType:'sequel'},{id:'1',relationType:'prequel'},{id:'2',relationType:'side_story'}];
const branchEdges=[{source:'0',target:'2'}];
const collisionMember={id:'only',x:0,y:0,labelWidth:80,boxHeight:104};
{
  const left=[{x:0,y:0},{x:100,y:0},{x:100,y:100},{x:0,y:100}];
  for(const penetration of [.1,.01,.001]) {
    const right=left.map(p=>({x:p.x+102-penetration,y:p.y}));
    const contact=context.relationGraphOverlap(left,right,true,2);
    assert.ok(contact && Math.abs(contact.depth-penetration)<1e-9,'contact correction fades with penetration instead of retaining a fixed kick near the wall');
  }
}
const visibleBody=context.relationGraphBodyBounds('node:only',[collisionMember]);
assert.equal(visibleBody.parts.length,2,'ordinary cover and displaced text have separate collision parts');
for(const above of [false,true]) {
  const root={...collisionMember,current:true,captionAbove:above,captionOffset:-98,captionTop:-96,
    captionMetrics:{titleTop:68,kindTop:86,titleHeight:15,kindHeight:15,kindWidth:44}};
  const body=context.relationGraphBodyBounds('node:root',[root]);
  const obstacle={outline:[{x:-25,y:110},{x:25,y:110},{x:25,y:130},{x:-25,y:130}]};
  assert.equal(context.relationGraphBodyContact(body,obstacle),null,'a clearly visible gap below a cover cannot repel a membrane');
  const beside={outline:[{x:-75,y:-14},{x:-55,y:-14},{x:-55,y:14},{x:-75,y:14}]};
  assert.equal(context.relationGraphBodyContact(body,beside),null,'empty space beside the cover is not occupied by the displaced caption');
  const touching={outline:[{x:-10,y:29},{x:10,y:29},{x:10,y:55},{x:-10,y:55}]};
  assert.ok(context.relationGraphBodyContact(body,touching),'actual cover contact still pushes the neighbouring body');
  const smallGap={outline:[{x:-10,y:37},{x:10,y:37},{x:10,y:55},{x:-10,y:55}]};
  assert.equal(context.relationGraphBodyContact(body,smallGap),null,'five pixels of real cover clearance does not start a body shove');
  const text=body.parts[1],textObstacle={outline:text.map(p=>({...p}))};
  assert.ok(context.relationGraphBodyContact(body,textObstacle),'titles remain protected at their displayed position');
}
const groupedA={id:'a',x:0,y:0,current:true,labelWidth:80,boxHeight:104,captionAbove:true},groupedB={id:'b',x:45,y:145,labelWidth:80,boxHeight:104,captionAbove:true};
const groupedCable=context.relationGraphCurve(groupedA,groupedB,{x:0,y:0,groupWidth:500,groupHeight:500},{kind:'sequel',adjacency:{a:['b'],b:['a']},obstacles:[{x:20,y:60,labelWidth:20,boxHeight:20}]});
assert.ok(((groupedCable.sx-groupedA.x)*45+(groupedCable.sy-groupedA.y)*145)/(32*Math.hypot(45,145))>.9,'anthology routes also leave the forward face rather than curling sideways');
const distantMembers=[{id:'d0',x:-60,y:0,labelWidth:80,boxHeight:104},{id:'d1',x:60,y:0,labelWidth:80,boxHeight:104}];
const unpressed=context.relationGraphGroupBounds(null,distantMembers);
const distantPressure={outline:[{x:1000,y:1000},{x:1200,y:1000},{x:1200,y:1200},{x:1000,y:1200}],members:[{x:1100,y:1100,labelWidth:80,boxHeight:104}]};
assert.equal(context.relationGraphGroupBounds(null,distantMembers,[distantPressure]).path,unpressed.path,'far-away pressure does not trigger contour resampling or cover projection');
const hasCrossing=points=>{
  const cross=(a,b,c)=>(b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x);
  for(let i=0;i<points.length;i++)for(let j=i+2;j<points.length;j++) {
    if(i===0 && j===points.length-1)continue;
    const a=points[i],b=points[(i+1)%points.length],c=points[j],d=points[(j+1)%points.length];
    if(cross(a,b,c)*cross(a,b,d)<-1e-7 && cross(c,d,a)*cross(c,d,b)<-1e-7)return true;
  }
  return false;
};
assert.ok(!hasCrossing(context.relationGraphUntangle([{x:0,y:0},{x:100,y:100},{x:0,y:100},{x:100,y:0}])),'contact contours cannot retain crossed loops');
for(const x of [-100,-50,0,50,100]) {
  const own=[{id:'a',x:-100,y:80,labelWidth:112,boxHeight:104},{id:'b',x:180,y:-40,labelWidth:112,boxHeight:104}];
  const other=[{id:'c',x,y:0,labelWidth:80,boxHeight:104},{id:'d',x:x+80,y:150,labelWidth:80,boxHeight:104}];
  const foreign={...context.relationGraphGroupBounds(null,other),members:other};
  const membrane=context.relationGraphGroupBounds(null,own,[foreign]);
  assert.ok(!hasCrossing(membrane.outline),'interpenetrating group drag does not invert its membrane at x='+x);
}
const third={id:'0',x:0,y:0,labelWidth:112,boxHeight:104},sideAbove={id:'2',x:50,y:-140,labelWidth:112,boxHeight:104},currentBelow={id:'current',x:50,y:140,current:true};
const thirdCaption=context.relationGraphCaptionPosition(third,[sideAbove,currentBelow]);
assert.equal(thirdCaption.x,0,'third-chapter caption stays centred despite upper and lower connections');
for(const above of [false,true])for(let i=0;i<24;i++) {
  const recipe={x:0,above,top:above?-96:32,bottom:above?-40:94};
  const work={id:'book',x:0,y:0,labelWidth:112,captionRecipe:recipe};
  const neighbours=Array.from({length:5},(_,j)=>({id:String(j),x:360*Math.cos(i+j),y:360*Math.sin(i+j),labelWidth:112}));
  const caption=context.relationGraphUpdateCaption(work,neighbours,{width:1000,height:1000},true);
  assert.equal(caption.x,0,'relationship and recommendation captions never move beside their covers');
  assert.equal(caption.above,above,'changing cable directions cannot move an unobstructed caption');
}
for(const above of [false,true])for(const dy of [-220,-120,120,220]) {
  const recipe={x:0,above,top:above?-96:32,bottom:above?-40:94};
  const dragged={id:'dazai',groupId:'pair',x:0,y:0,labelWidth:112,boxHeight:124,captionRecipe:recipe};
  const storm={id:'storm',x:160,y:dy,labelWidth:112,boxHeight:124};
  assert.equal(context.relationGraphUpdateCaption(dragged,[storm],{width:1000},true).above,above,
    'moving Dazai above or below STORMBRINGER does not flip a caption whose actual space is clear');
}
{
  const n={id:'dazai',groupId:'pair',x:0,y:80,labelWidth:112,boxHeight:124,
    captionRecipe:{x:0,above:false,top:32,bottom:94}};
  const bottom=context.relationGraphUpdateCaption(n,[],{width:600,height:400,requestedY:164},true);
  assert.equal(bottom.above,true,'a bottom-edge pointer moves the caption up before its old lower caption clamps the book short of the edge');
  n.captionRecipe=bottom;
  assert.equal(context.relationGraphUpdateCaption(n,[],{width:600,height:400,requestedY:164},true).above,true,'continued bottom pressure keeps the upper caption stable');
  n.y=-80;
  assert.equal(context.relationGraphUpdateCaption(n,[],{width:600,height:400,requestedY:-164},true).above,false,'the top edge moves the caption below instead of pinning it outside');
}
for(const width of [320,480,760])for(const side of [-98,98]) {
  const previous={x:side,above:false,top:32,bottom:94};
  const moved={id:'main',x:width/4,y:0,labelWidth:112,boxHeight:104,captionRecipe:previous};
  const current={id:'current',current:true,x:-width/4,y:0,labelWidth:112,boxHeight:120};
  const returned=context.relationGraphUpdateCaption(moved,[current],{width},true);
  assert.equal(returned.x,0,'moving a two-work graph clear returns a displaced title to its cover at width '+width);
  assert.equal(returned.above,false,'an unobstructed lower caption is preferred');
  const second={...current,id:'other',y:-180};
  assert.equal(context.relationGraphCaptionPosition(moved,[current,second],{width}).x,0,
    'multiple connections also release a sideways caption when the centred slot becomes clear');
  moved.captionRecipe=returned;
  assert.equal(context.relationGraphUpdateCaption(moved,[current],{width}),returned,'the restored title remains stable after release');
  for(const [x,y] of [[0,-60],[40,-20],[-40,20],[0,60]]) {
    const nearby={...current,x:moved.x+x,y:moved.y+y};
    assert.equal(context.relationGraphCaptionPosition({...moved,captionRecipe:previous},[nearby],{width}).x,0,
      'a single connected work keeps its title above or below the cover even at close angles');
  }
}
third.captionOffset=thirdCaption.x;
const thirdCable=context.relationGraphCurve(third,sideAbove,null,{kind:'main_story',adjacency:{'0':['2','current'],'2':['0']}});
for(let i=0;i<=40;i++) {
  const p=thirdCable.point(i/40),deviation=Math.abs(p.x*(-140)-p.y*50)/Math.hypot(50,140);
  assert.ok(deviation<20,'third chapter to side story retains a shallow arc instead of detouring around its own caption');
}
for(let i=0;i<48;i++) {
  const theta=i*Math.PI/24,a={id:'a',x:0,y:0,current:true,labelWidth:112,boxHeight:110,captionAbove:true},
    b={id:'b',x:Math.cos(theta)*240,y:Math.sin(theta)*240,labelWidth:112,boxHeight:104,captionAbove:true};
  const cable=context.relationGraphCurve(a,b,null,{spoke:true,adjacency:{a:['b'],b:['a']},obstacles:[{x:b.x*.5,y:b.y*.5,labelWidth:30,boxHeight:40}]});
  const facing=((cable.sx-a.x)*b.x+(cable.sy-a.y)*b.y)/(32*240);
  assert.ok(facing>.9,'runtime detours leave the forward face of the cover rather than curling around it');
  const dx=cable.ex-cable.sx,dy=cable.ey-cable.sy,length=Math.hypot(dx,dy);
  for(const p of [cable.c1,cable.c2])assert.ok(Math.abs(-(p.x-cable.sx)*dy+(p.y-cable.sy)*dx)/length<=40.001,'runtime detours retain a bounded soft curve');
}
const uprightSide={id:'0',x:0,y:-130,labelWidth:112,boxHeight:104};
const uprightCurrent={id:'1',x:0,y:90,current:true,labelWidth:112,boxHeight:110};
uprightSide.captionAbove=Boolean(context.relationGraphCaptionOffset(uprightSide,[uprightCurrent]));
uprightCurrent.captionAbove=Boolean(context.relationGraphCaptionOffset(uprightCurrent,[uprightSide]));
assert.ok(uprightSide.captionAbove,'the caption moves above a vertical cable instead of forcing a large curve');
const uprightCable=context.relationGraphCurve(uprightSide,uprightCurrent,null,{kind:'side_story'});
for(let i=0;i<=20;i++)assert.ok(Math.abs(uprightCable.point(i/20).x)<20,'reported vertical relationship uses a bounded shallow arc');
assert.ok(Math.abs(uprightCable.point(.5).x)>5,'vertical cable retains a visibly soft curve');
for(const [dx,dy] of [[110,0],[100,60],[-100,60],[100,-60],[-100,-60]]) {
  const a={id:'a',x:0,y:0,current:true,labelWidth:112,boxHeight:110},b={id:'b',x:dx,y:dy,labelWidth:112,boxHeight:104};
  const compactCable=context.relationGraphCurve(a,b,null,{spoke:true,state:{sourceAngle:-Math.PI,targetAngle:Math.PI/2}});
  const length=Math.hypot(dx,dy);
  for(let i=0;i<=20;i++) {
    const p=compactCable.point(i/20),deviation=Math.abs(p.x*dy-p.y*dx)/length;
    assert.ok(deviation<5,'short cables straighten along the current angle instead of retaining a wide arc');
  }
}
const hubWorks=[{id:'current',current:true},{id:'0',relationType:'prequel'},{id:'1',relationType:'sequel'},{id:'2',relationType:'side_story'}];
const hubEdges=[{source:'0',target:'2'},{source:'2',target:'0'},{source:'1',target:'2'},{source:'2',target:'1'}];
for(const width of [240,330,440,760,1080]) {
  const hubLayout=context.relationGraphBranchLayout(hubWorks,width,hubEdges),p=hubLayout.positions;
  assert.ok(p[1][0]<p[0][0] && p[0][0]<p[2][0],'prequel is left and sequel is right at width '+width);
  assert.ok(p[3][1]<Math.min(p[1][1],p[2][1]),'side story has its own row above the main works at width '+width);
}
const shuffledHub=[hubWorks[0],hubWorks[3],hubWorks[1],hubWorks[2]];
const shuffledLayout=context.relationGraphBranchLayout(shuffledHub,760,hubEdges);
for(const width of [320,390,760,1080]) {
  const anthology=[{id:'current',current:true},{id:'main',relationType:'main_story'},
    {id:'previous',relationType:'prequel'},{id:'next',relationType:'sequel'}];
  const placed=context.relationGraphBranchLayout(anthology,width,[{source:'main',target:'previous'},{source:'main',target:'next'}]).positions;
  assert.ok(placed[2][0]<placed[0][0] && placed[3][0]>placed[0][0],'anthology prequel stays left and sequel stays right even when both connect to the main work');
  assert.ok(placed[1][1]<Math.min(placed[2][1],placed[3][1]),'the main work occupies a separate upper branch');
  const trio=context.relationGraphBranchLayout([anthology[0],anthology[2],anthology[3]],width,[]).positions;
  assert.ok(trio[1][0]<trio[0][0] && trio[2][0]>trio[0][0],'three-work chronology also reads left to right');
}
assert.ok(shuffledLayout.positions[1][1]<shuffledLayout.positions[0][1],'query order does not move the side story below the current work');
const stackedTop={id:'0',x:180,y:-180,labelWidth:112,boxHeight:104};
const stackedMiddle={id:'1',x:180,y:0,labelWidth:112,boxHeight:104};
const stackedBottom={id:'2',x:180,y:180,labelWidth:112,boxHeight:104};
const stackedRoot={id:'current',x:-180,y:0,current:true,labelWidth:112,boxHeight:110};
const longCable=context.relationGraphCurve(stackedTop,stackedBottom,null,{kind:'side_story',obstacles:[stackedMiddle,stackedRoot],limits:{width:760,height:580},spokes:[{a:stackedRoot,b:stackedMiddle}]});
const longSamples=Array.from({length:33},(_,i)=>longCable.point(i/32));
const shortOptions={kind:'side_story',obstacles:[stackedTop,stackedRoot],limits:{width:760,height:580}};
const originalShort=context.relationGraphCurve(stackedMiddle,stackedBottom,null,shortOptions);
const separatedShort=context.relationGraphCurve(stackedMiddle,stackedBottom,null,{...shortOptions,cables:[{samples:longSamples,shared:[stackedBottom]}]});
const overlapCost=curve=>Array.from({length:13},(_,i)=>curve.point((i+2)/16)).filter(p=>Math.hypot(p.x-stackedBottom.x,p.y-stackedBottom.y)>=42)
  .reduce((sum,p)=>sum+Math.max(0,16-Math.min(...longSamples.map(q=>Math.hypot(p.x-q.x,p.y-q.y)))),0);
assert.ok(overlapCost(separatedShort)<=overlapCost(originalShort),'stacked first/third chapter cables keep their paths separate, including when a compact route is already clear');
const shortSamples=Array.from({length:33},(_,i)=>originalShort.point(i/32));
const spacedLong=context.relationGraphCurve(stackedTop,stackedBottom,null,{kind:'side_story',obstacles:[stackedMiddle,stackedRoot],limits:{width:760,height:580},
  cables:[{samples:shortSamples,shared:[stackedBottom],anchors:[{id:stackedBottom.id,point:shortSamples[32]}]}]});
assert.ok(Math.hypot(spacedLong.ex-originalShort.ex,spacedLong.ey-originalShort.ey)>=16,'shared target arrows retain visible spacing');
const videoRoot={id:'current',x:0,y:0,current:true,labelWidth:112,boxHeight:110};
const videoPeer={id:'1',x:160,y:-95,labelWidth:112,boxHeight:104};
const videoSide={id:'2',x:290,y:0,labelWidth:112,boxHeight:104};
const videoAdjacency={current:['1','2'],'1':['current','2'],'2':['current','1']};
const videoRouting={adjacency:videoAdjacency,neighbours:[videoRoot,videoPeer,videoSide]};
const videoSpoke=context.relationGraphCurve(videoRoot,videoSide,null,{...videoRouting,spoke:true});
const videoPeerCable=context.relationGraphCurve(videoPeer,videoSide,null,{...videoRouting,kind:'side_story'});
assert.ok(Math.hypot(videoSpoke.ex-videoPeerCable.ex,videoSpoke.ey-videoPeerCable.ey)>8,'video final frame uses separate attachment points for incoming cables');
const rotatingState={};
for(let frame=0;frame<240;frame++) {
  const theta=frame*Math.PI/120,distance=90+frame%160;
  const a={id:'a',x:0,y:0,labelWidth:112,boxHeight:104},b={id:'b',x:Math.cos(theta)*distance,y:Math.sin(theta)*distance,labelWidth:112,boxHeight:104};
  const cable=context.relationGraphCurve(a,b,null,{kind:'main_story',state:rotatingState,frame});
  const dx=cable.ex-cable.sx,dy=cable.ey-cable.sy;let previous=-Infinity;
  const side=control=>-(control.x-cable.sx)*dy+(control.y-cable.sy)*dx;
  assert.ok(side(cable.c1)*side(cable.c2)>=-1e-6,'moving cable never develops opposing S-shaped control bends');
  for(let i=0;i<=40;i++) {
    const p=cable.point(i/40),progress=(p.x-cable.sx)*dx+(p.y-cable.sy)*dy;
    assert.ok(progress>=previous-1e-7,'moving cable cannot fold backwards or curl into a loop');previous=progress;
  }
}
const stableBowState={};
for(const x of [240,120,60,20,0,-20,-60,-120,-240]) {
  const cable=context.relationGraphCurve({id:'root',x:0,y:0,current:true,captionAbove:true},{id:'peer',x,y:-220,labelWidth:112,boxHeight:104},null,{spoke:true,state:stableBowState});
  assert.equal(stableBowState.bowSign,stableBowState.initialSign??(stableBowState.initialSign=stableBowState.bowSign),'moving across the vertical axis preserves the shallow arc direction');
}
for(const y of [-95,95]) {
  const root={id:'current',x:-150,y:0,current:true,labelWidth:112,boxHeight:110};
  const peer={id:'p',x:80,y,labelWidth:112,boxHeight:104};
  const spoke=context.relationGraphCurve(root,peer,null,{spoke:true});
  assert.ok(spoke.c1 && spoke.c2,'current-work spokes use the same smooth cubic family as peer cables');
  const cross=Math.max(...[.25,.5,.75].map(t=>{const p=spoke.point(t);return Math.abs((p.x-root.x)*y-(p.y-root.y)*(peer.x-root.x))/Math.hypot(peer.x-root.x,y);}));
  assert.ok(cross>5 && cross<20,'unobstructed current-work connections use a gentle bounded arc');
  for(let i=1;i<100;i++) {
    const p=spoke.point(i/100);
    for(const n of [root,peer])assert.ok(!(Math.abs(p.x-n.x)<n.labelWidth/2 && p.y>n.y+32 && p.y<n.y+n.boxHeight-24),'rounded current connections avoid titles');
  }
}
const titlePeers=[{id:'1',x:220,y:-82,labelWidth:112,boxHeight:104},{id:'2',x:220,y:82,labelWidth:112,boxHeight:104}];
const titleCable=context.relationGraphCurve(...titlePeers,null,{kind:'main_story'});
for(let i=1;i<100;i++) {
  const p=titleCable.point(i/100);
  for(const n of titlePeers)assert.ok(!(Math.abs(p.x-n.x)<n.labelWidth/2+3 && p.y>n.y+30 && p.y<n.y+n.boxHeight-24),'peer cable avoids both title rectangles');
}
const branchLayout=context.relationGraphBranchLayout(branches,840,branchEdges);
assert.ok(branchLayout.positions[2][0]<branchLayout.positions[0][0],'isolated prequel is before current work');
assert.ok(branchLayout.positions[1][0]>branchLayout.positions[0][0] && branchLayout.positions[3][1]<branchLayout.positions[0][1],'successor is right and side story occupies its own upper row');
for(const width of [380,400,440,500,600,639,640,840]) {
  const compact=context.relationGraphBranchLayout(branches,width,branchEdges);
  const [x,y]=compact.positions[1],[ex,ey]=compact.positions[3];
  const a={id:'1',x,y,labelWidth:compact.labelWidth,boxHeight:104},b={id:'2',x:ex,y:ey,labelWidth:compact.labelWidth,boxHeight:104};
  const root={x:compact.positions[0][0],y:compact.positions[0][1],labelWidth:112,boxHeight:104};
  const route=context.relationGraphCurve(a,b,null,{kind:'side_story',limits:{width,height:compact.height},obstacles:[root],spokes:[{a:root,b:a},{a:root,b:b}]});
  for(let t=0;t<=1;t+=.02)assert.ok(route.point(t).x<width/2-5,'outer cable stays within compact stage at width '+width);
}
const loose=branches.map((n,i)=>({...softNode(n.id,...branchLayout.positions[i]),...n}));
const looseLinks=context.relationGraphSoftLinks(loose,branchEdges);
assert.equal(looseLinks.length,1,'ordinary cohesion only follows an explicitly registered peer relation');
assert.ok(!looseLinks.some(l=>l.a.id==='1'||l.b.id==='1'),'unrelated prequel is not tied to successor or side story');
const looseBefore=loose.map(n=>({x:n.x,y:n.y}));loose[2].x-=50;loose[2].y+=40;
for(let i=0;i<20;i++)context.relationGraphSoftStep(loose,looseLinks,loose[2],{width:1000,height:800});
assert.equal(loose[1].x,looseBefore[1].x,'moving the isolated prequel does not pull its unrelated successor');
const slime=[softNode('a0',-180,-30,'a'),softNode('a1',-180,90,'a'),softNode('a2',-60,30,'a'),
  softNode('b0',90,-30,'b'),softNode('b1',90,90,'b'),softNode('b2',210,30,'b')];
const edgeContact=[softNode('held',200,0,'p'),softNode('p',70,0,'p'),softNode('q0',240,-80,'q'),softNode('q1',240,80,'q')];
context.relationGraphSoftStep(edgeContact,context.relationGraphSoftLinks(edgeContact),edgeContact[0],{width:600,height:700});
assert.ok(edgeContact[0].x<200,'when the other group reaches the wall the held node yields elastically instead of punching through it');
const openGap=[softNode('left',-350,0),softNode('right',350,0),softNode('g0',0,-100,'g'),softNode('g1',0,100,'g')];
assert.equal(context.relationGraphSoftStep(openGap,context.relationGraphSoftLinks(openGap),null,{width:1200,height:1000}),0,
  'unrelated ordinary works do not create a giant invisible body across the empty gap');
const softLinks=context.relationGraphSoftLinks(slime),original=slime.map(n=>({x:n.x,y:n.y}));
slime[2].x=80;
let largestStep=0,peakDeformation=0;
for(let i=0;i<80;i++) {
  largestStep=Math.max(largestStep,context.relationGraphSoftStep(slime,softLinks,slime[2],{width:1000,height:800}));
  const local=slime.slice(3).map((n,j)=>Math.hypot(n.x-original[j+3].x,n.y-original[j+3].y));
  peakDeformation=Math.max(peakDeformation,Math.max(...local)-Math.min(...local));
}
assert.equal(slime[2].x,80,'held work follows the pointer without a collision veto');
assert.equal(slime[2].y,30);
const displacement=slime.slice(3).map((n,i)=>Math.hypot(n.x-original[i+3].x,n.y-original[i+3].y));
assert.ok(Math.max(...displacement)>10,'contact pushes the other body');
assert.ok(peakDeformation>3,'contact deforms the body before pressure relaxes');
assert.ok(largestStep<45,'neighbours move continuously instead of teleporting to a distant empty slot');
let finalMovement=0;
for(let i=0;i<240;i++)finalMovement=context.relationGraphSoftStep(slime,softLinks,null,{width:1000,height:800});
assert.ok(finalMovement<.1,'released bodies settle without persistent vibration: '+finalMovement);
const baseSlime=context.relationGraphGroupBounds(null,[softNode('s0',-60,0,'s'),softNode('s1',60,0,'s')]);
const pressureShape={outline:[{x:100,y:-100},{x:220,y:-100},{x:220,y:180},{x:100,y:180}]};
const dented=context.relationGraphGroupBounds(null,[softNode('s0',-60,0,'s'),softNode('s1',60,0,'s')],[pressureShape]);
assert.notEqual(dented.path,baseSlime.path,'contact changes the boundary locally');
assert.equal(dented.outline.length,64,'continuous boundary samples permit dents');
console.log('PASS free pointer tracking, local body pressure, deformation, bounded motion, and settling');
for(const id of ['0','1','7','12'])for(let time=0;time<120;time+=.1) {
  const p=context.relationGraphDrift(id,time),q=context.relationGraphDrift(id,time+1/60);
  assert.ok(Math.abs(p.x)<=4.001 && Math.abs(p.y)<=3.201,'floating stays within a few pixels');
  assert.ok(Math.hypot(q.x-p.x,q.y-p.y)<.06,'floating has no frame-to-frame jumps');
}
assert.notDeepEqual(context.relationGraphDrift('0',10),context.relationGraphDrift('1',10),'works drift independently');
assert.equal(context.relationGraphCanDrift({id:'wan'}),false,'ordinary spin-off does not move its cable at idle');
assert.equal(context.relationGraphCanDrift({groupId:'anthology'}),true,'soft group members retain gentle floating');
assert.equal(context.relationGraphCanDrift({id:'recommendation'},true),true,'recommendation works retain gentle idle motion');
assert.equal(context.relationGraphCanDrift({groupId:'family',current:true}),false,'current work has no automatic drift');
{
  const blocked={n:{x:-60,y:40},targetX:-100,targetY:100,vx:-12,vy:15};
  context.relationGraphSetDragTarget(blocked,{x:-100,y:100});
  context.relationGraphReconcileDrag(blocked,{x:-80,y:60});
  assert.equal(blocked.targetX,-60);assert.equal(blocked.targetY,40);
  assert.equal(blocked.vx,0);assert.equal(blocked.vy,0);
  for(let move=0;move<120;move++) {
    assert.equal(context.relationGraphSetDragTarget(blocked,{x:-100,y:100}),false,'repeated out-of-bounds corner input cannot rearm a blocked target');
    assert.equal(blocked.targetX,-60);assert.equal(blocked.targetY,40);
  }
  context.relationGraphDragSpring(blocked.n,blocked);
  assert.equal(blocked.n.x,-60);assert.equal(blocked.n.y,40,'blocked pointer rests at the reachable point instead of repeatedly fighting the wall');
  assert.equal(context.relationGraphSetDragTarget(blocked,{x:-100,y:80}),true,'moving along the wall releases the old contact projection');
  assert.equal(blocked.targetX,-100);assert.equal(blocked.targetY,80);
}
const elastic={x:0,y:0,vx:0,vy:0},elasticTarget={x:80,y:-40};
context.relationGraphElasticPoint(elastic,elasticTarget);
assert.ok(elastic.x>0 && elastic.x<80,'boundary follows with elastic lag');
for(let i=0;i<150;i++)context.relationGraphElasticPoint(elastic,elasticTarget);
assert.ok(Math.hypot(elastic.x-80,elastic.y+40)<.01,'elastic boundary stops ringing after movement');
context.relationGraphElasticPoint(elastic,{x:0,y:0},true);
assert.equal(elastic.x,0,'reduced motion disables boundary spring');
const cable=context.relationGraphGroupConnector({id:'current',x:-300,y:0,current:true},
  {id:'g',x:0,y:0,group:true,outline:[{x:-100,y:-100},{x:100,y:-100},{x:100,y:100},{x:-100,y:100}]});
assert.ok(cable.c1 && cable.c2,'group connector uses a cubic cable with separate end tangents');
assert.ok(Math.hypot(cable.point(0).x-cable.sx,cable.point(0).y-cable.sy)<.001);
assert.ok(Math.hypot(cable.point(1).x-cable.ex,cable.point(1).y-cable.ey)<.001);
console.log('PASS slow bounded independent drift, elastic damping, reduced motion, and cubic group cables');
const cableState={};
const cableBody={id:'g',x:0,y:0,group:true,outline:[{x:-100,y:-100},{x:100,y:-100},{x:100,y:100},{x:-100,y:100}]};
context.relationGraphGroupConnector({x:-300,y:0},cableBody,{state:cableState,idle:true});
const anchorBefore={...cableState.cableAnchor},portBefore=cableState.portIndex;
context.relationGraphGroupConnector({x:-300,y:0},{...cableBody,outline:cableBody.outline.map(p=>({x:p.x+1,y:p.y+.4}))},{state:cableState,idle:true});
assert.ok(Math.hypot(cableState.cableAnchor.x-anchorBefore.x,cableState.cableAnchor.y-anchorBefore.y)<2,'idle attachment follows the nearby facing boundary');
assert.equal(cableState.portIndex,portBefore,'idle drift does not flip the source port');
assert.equal(baseSlime.outline[0].y,Math.min(...baseSlime.outline.map(p=>p.y)),'boundary sampling starts at the stable top of the title');
for(const width of [240,320,390,640])for(const count of [2,3,4]) {
  const small=context.relationGraphLayout(count,width),boxes=[];
  small.positions.forEach(([x,y],i)=>{
    const box={left:x-small.labelWidth/2,right:x+small.labelWidth/2,top:y-(i?24:30),bottom:y+108};
    assert.ok(box.left>=-width/2 && box.right<=width/2 && box.top>=-small.height/2 && box.bottom<=small.height/2,'small graph stays inside viewport');
    assert.ok(!boxes.some(b=>box.left<b.right && box.right>b.left && box.top<b.bottom && box.bottom>b.top),'small graph covers and titles do not overlap');
    boxes.push(box);
  });
}
const avoidCurrent=context.relationGraphCurve({id:'0',x:-130,y:0,labelWidth:112,boxHeight:108},
  {id:'1',x:130,y:0,labelWidth:112,boxHeight:108},null,
  {kind:'main_story',lane:32,obstacles:[{x:0,y:15,labelWidth:112,boxHeight:125}]});
assert.ok(avoidCurrent.point(.5).y < -19 || avoidCurrent.point(.5).y > 124,'peer relation bends around current work rather than through its cover');
console.log('PASS simple directional labels, small layouts, and avoiding current cover');
const dragNode={x:0,y:0},dragSpring={targetX:60,targetY:20,vx:0,vy:0};
for(const sx of [-1,1])for(const sy of [-1,1]) {
  const n={id:'held',groupId:'pair',x:sx*1000,y:sy*1000,labelWidth:112,boxHeight:124,captionAbove:sy>0,captionTop:-94};
  const limits={width:800,height:600};
  context.relationGraphFitDragNode(n,limits);
  const accepted={x:n.x,y:n.y};
  for(let frame=0;frame<120;frame++) {
    const target={...n,x:sx*1000,y:sy*1000};
    context.relationGraphFitDragNode(target,limits);
    const spring={targetX:target.x,targetY:target.y,vx:0,vy:0};
    context.relationGraphDragSpring(n,spring,false,limits);
    context.relationGraphFitBody([n],n,limits);
    assert.ok(Math.hypot(n.x-accepted.x,n.y-accepted.y)<1e-8,'repeated pointer pressure and membrane fitting agree at corner '+sx+','+sy);
  }
}
{
  const n={id:'wan',x:-300,y:-250,labelWidth:112,boxHeight:110,captionAbove:true,captionTop:-92,captionOffset:0,
    captionMetrics:{titleTop:56,kindTop:75,titleHeight:16,kindHeight:16,kindWidth:40}};
  const limits={width:600,height:400};
  context.relationGraphFitNode(n,limits);
  const fitted={x:n.x,y:n.y},bounds=context.relationGraphBodyBounds('node:wan',[n]).outline;
  assert.ok(Math.min(...bounds.map(p=>p.x))>=-288 && Math.min(...bounds.map(p=>p.y))>=-188,'node viewport fit protects its actual above-title and cover');
  for(let frame=0;frame<120;frame++)context.relationGraphSoftStep([n],[],null,limits);
  assert.ok(Math.hypot(n.x-fitted.x,n.y-fitted.y)<1e-8,'physics and repeated rendering cannot fight over different caption bounds');
  const spring={targetX:-500,targetY:-500,vx:0,vy:0};
  for(let frame=0;frame<120;frame++)context.relationGraphDragSpring(n,spring,false,limits);
  assert.ok(Math.hypot(n.x-fitted.x,n.y-fitted.y)<1e-8,'drag uses the same reachable viewport boundary as collision fitting');
}
{
  const n={x:20,y:50},released=context.relationGraphReleaseSpring({n,targetX:150,targetY:-120,vx:22,vy:-22});
  let maximum=0;
  for(let i=0;i<120;i++) {
    context.relationGraphDragSpring(n,released);
    maximum=Math.max(maximum,Math.hypot(n.x-20,n.y-50));
  }
  assert.ok(maximum<6,'a fast release retains a small rebound without coasting to an obsolete pointer target');
  assert.ok(Math.hypot(n.x-20,n.y-50)<.13,'a released work rests at its accepted visible position');
}
context.relationGraphDragSpring(dragNode,dragSpring);
assert.ok(dragNode.x>0 && dragNode.x<60,'drag uses a spring rather than moving immediately to the cursor');
let overshoot=0;
for(let i=0;i<120;i++){context.relationGraphDragSpring(dragNode,dragSpring);overshoot=Math.max(overshoot,dragNode.x-60);}
assert.ok(overshoot>1 && overshoot<25,'drag spring has controlled elastic rebound');
assert.ok(Math.hypot(dragNode.x-60,dragNode.y-20)<.13,'released spring settles');
const foreignCover={id:'foreign',x:-260,y:0,current:true,labelWidth:112,boxHeight:110};
const foreignBody={members:[foreignCover],...context.relationGraphGroupBounds({caption:false},[foreignCover])};
const protective=context.relationGraphGroupBounds(null,resting,[foreignBody]);
assert.equal(protective.outline.length,128,'cover contact has a denser deformable boundary');
for(let i=0;i<protective.outline.length;i++) {
  const p=protective.outline[i],prev=protective.outline[(i+127)%128],next=protective.outline[(i+1)%128];
  assert.ok(context.relationGraphCoverDistance(p,foreignCover)>11.9,'boundary vertices clear the cover');
  const start={x:(prev.x+p.x)/2,y:(prev.y+p.y)/2},end={x:(next.x+p.x)/2,y:(next.y+p.y)/2};
  for(let t=0;t<=1;t+=.125){const q={x:(1-t)**2*start.x+2*(1-t)*t*p.x+t*t*end.x,y:(1-t)**2*start.y+2*(1-t)*t*p.y+t*t*end.y};assert.ok(context.relationGraphCoverDistance(q,foreignCover)>0,'rendered boundary cannot cut through the cover or title');}
}
const forwardCable=context.relationGraphGroupConnector({id:'root',x:-300,y:0,current:true,labelWidth:112,boxHeight:110},cableBody,{obstacles:[{x:-180,y:0,labelWidth:60,boxHeight:90}]});
assert.ok(Math.abs(Math.hypot(forwardCable.sx+300,forwardCable.sy)-30)<1e-8,'group cable starts at the solid current-cover ring, not its detached orbit decoration');
assert.ok((forwardCable.c1.x-forwardCable.sx)*(forwardCable.ex+300)+(forwardCable.c1.y-forwardCable.sy)*forwardCable.ey>0,'root cable starts toward the group rather than doubling back');
console.log('PASS drag elasticity, release damping, protected contact curves, and forward cable departure');
{
  const members=[{id:'a',groupId:'g',x:-65,y:-150,labelWidth:80,boxHeight:104},
    {id:'b',groupId:'g',x:65,y:-150,labelWidth:80,boxHeight:104}];
  context.relationGraphSoftStep(members,[],members[0],{width:500,height:430});
  const skin=context.relationGraphBodyBounds('g',members).outline;
  assert.ok(Math.min(...skin.map(p=>p.y))>=-430/2+11.99,'pushing a group against the compact viewport keeps the membrane heading inside');
  const curve=context.relationGraphBookCable({id:'a',x:-140,y:0},{id:'b',x:140,y:0},{adjacency:{},state:{}},null);
  const total=context.relationGraphEndMark(curve,0).total,mid=context.relationGraphEndMark(curve,total*.5);
  assert.ok(Math.abs(mid.x)<4,'relation descriptions can anchor at the middle of their own cable');
  const rectangle=(left,right)=>[{x:left,y:-60},{x:right,y:-60},{x:right,y:60},{x:left,y:60}];
  const [leftSkin,rightSkin]=context.relationGraphContactContours(
    context.relationGraphContourBounds(rectangle(-140,25)),context.relationGraphContourBounds(rectangle(-25,140)),
    rectangle(-130,-10),rectangle(10,130));
  assert.ok(Math.max(...leftSkin.outline.map(p=>p.x))<=-2 && Math.min(...rightSkin.outline.map(p=>p.x))>=2,'touching skins keep a separate contact surface while the physical bodies settle');
  const limited=context.relationGraphViewportContour(context.relationGraphContourBounds(rectangle(-200,200)),300,100);
  assert.ok(limited.outline.every(p=>Math.abs(p.x)<=142.001 && Math.abs(p.y)<=42.001),'pressure cannot stretch the rendered membrane outside a compact viewport');
}
for(const [x,y] of [[-170,0],[-115,-50],[0,-90],[120,0],[170,70],[0,60]]) {
  const cover={...foreignCover,x,y};
  const body={members:[cover],...context.relationGraphGroupBounds({caption:false},[cover])};
  const outline=context.relationGraphGroupBounds(null,resting,[body]).outline;
  const overlapping=resting.some(n=>Math.abs(n.x-cover.x)<(n.labelWidth+cover.labelWidth)/2+24 && Math.abs(n.y-cover.y)<110);
  if(overlapping) {
    const inside=p=>{let result=false;for(let i=0,j=outline.length-1;i<outline.length;j=i++) {const a=outline[i],b=outline[j];if((a.y>p.y)!==(b.y>p.y)&&p.x<(b.x-a.x)*(p.y-a.y)/(b.y-a.y)+a.x)result=!result;}return result;};
    for(const n of resting)for(const dx of [-n.labelWidth/2,n.labelWidth/2])for(const dy of [-24,80])assert.ok(inside({x:n.x+dx,y:n.y+dy}),'temporarily overlapping bodies must not cut their own books or captions out');
    continue;
  }
  outline.forEach((p,i)=>{
    const prev=outline[(i+outline.length-1)%outline.length],next=outline[(i+1)%outline.length];
    for(let t=0;t<=1;t+=.25) {
      const a={x:(prev.x+p.x)/2,y:(prev.y+p.y)/2},b={x:(next.x+p.x)/2,y:(next.y+p.y)/2};
      const q={x:(1-t)**2*a.x+2*(1-t)*t*p.x+t*t*b.x,y:(1-t)**2*a.y+2*(1-t)*t*p.y+t*t*b.y};
      assert.ok(context.relationGraphCoverDistance(q,cover)>0,'contact curve clears foreign cover from '+x+','+y);
    }
  });
}
const reversedCable=context.relationGraphGroupConnector({x:300,y:0,labelWidth:112,boxHeight:110},cableBody,{state:cableState,idle:true});
assert.ok(reversedCable.c1.x<reversedCable.sx,'moving the root past a group invalidates its old attachment side');
const tightCable=context.relationGraphGroupConnector({id:'root',x:0,y:0,current:true,labelWidth:112,boxHeight:101},
  {id:'g',x:400,y:0,group:true,outline:[{x:240,y:-200},{x:550,y:-200},{x:550,y:220},{x:240,y:220}]},
  {obstacles:[{x:0,y:-120,labelWidth:112,boxHeight:89},{x:127,y:0,labelWidth:112,boxHeight:89}]});
assert.ok(tightCable.score<10000,'diagonal departure clears the actual crowded root layout');
const staleAttachment={portIndex:0,cableAnchor:{x:100,y:0}};
const facingCable=context.relationGraphGroupConnector({x:-300,y:0,current:true,labelWidth:112,boxHeight:101},cableBody,{state:staleAttachment,idle:true});
assert.ok(facingCable.ex < -95,'stale attachment cannot select the far boundary through the group');
// The reported video has a lower group and a top-right work sharing the root.
{
const videoRoot={id:'current',current:true,x:0,y:0,labelWidth:112,boxHeight:104};
const videoNeighbours=[{id:'up',x:0,y:-120},{id:'left',x:-125,y:0},{id:'right',x:125,y:0},
  {id:'upper-left',x:-125,y:-120},{id:'upper-right',x:125,y:-120},{id:'lower',group:true,x:0,y:300}];
const videoCaption=context.relationGraphUpdateCaption(videoRoot,videoNeighbours);
assert.ok(videoCaption.x===0 && !videoCaption.above,'current badge stays below even when a group cable points down');
const belowLeft=context.relationGraphCaptionPosition({...videoNeighbours[1],labelWidth:112},[{...videoRoot,captionOffset:videoCaption.x}]);
assert.equal(belowLeft.x,0,'neighbouring caption also stays centred on its cover');
for(const [x,y] of [[0,220],[120,220],[280,0],[-160,-120]]) {
  const body={id:'lower',group:true,x,y,outline:[{x:x-75,y:y-45},{x:x+75,y:y-45},{x:x+75,y:y+45},{x:x-75,y:y+45}]};
  const curve=context.relationGraphGroupConnector({...videoRoot,captionOffset:videoCaption.x},body,{neighbours:videoNeighbours});
  const dx=curve.ex-curve.sx,dy=curve.ey-curve.sy,length=Math.hypot(dx,dy);
  for(let i=0;i<20;i++) {
    const p=curve.point(i/20),q=curve.point((i+1)/20);
    assert.ok((q.x-p.x)*dx+(q.y-p.y)*dy>0,'group cable always advances toward its boundary during movement');
    assert.ok(Math.abs((p.x-curve.sx)*dy-(p.y-curve.sy)*dx)/length<=15.001,'group cable retains a shallow bend');
  }
}
}
const sharedState={};
{
  const a={id:'0',x:0,y:0,labelWidth:112,boxHeight:104,captionOffset:-98},
    b={id:'1',x:0,y:-200,labelWidth:112,boxHeight:104,captionAbove:true};
  const occupied={samples:Array.from({length:33},(_,i)=>({x:0,y:-26-i*148/32})),shared:[a,b],
    anchors:[{id:a.id,point:{x:0,y:-26}},{id:b.id,point:{x:0,y:-174}}]};
  for(const [x,y] of [[0,-200],[20,-160],[-40,-240],[110,-100],[-130,-70]]) {
    const curve=context.relationGraphBookCable(a,{...b,x,y},{adjacency:{'0':['1'],'1':['0']},neighbours:[a,b],cables:[occupied],state:{}},null);
    const dx=curve.ex-curve.sx,dy=curve.ey-curve.sy,chord=Math.hypot(dx,dy);
    for(const [vx,vy] of [[curve.c1.x-curve.sx,curve.c1.y-curve.sy],[curve.ex-curve.c2.x,curve.ey-curve.c2.y]]) {
      const forward=(vx*dx+vy*dy)/chord,side=Math.abs(vx*dy-vy*dx)/chord;
      assert.ok(forward>0 && side<=forward*.400001,'occupied vertical cables approach both covers without hooked end tangents');
    }
  }
}
const sharedForward=context.relationGraphCurve(positions['0'],positions['1'],groupBox,{kind:'sequel',state:sharedState,frame:1});
const sharedReverse=context.relationGraphCurve(positions['1'],positions['0'],groupBox,{kind:'main_story',state:sharedState,frame:1});
for(const cable of [sharedForward,sharedReverse]) {
  const tip=context.relationGraphEndMark(cable,0);
  assert.ok(Math.hypot(tip.x-cable.ex,tip.y-cable.ey)<1e-9,'arrow tip reaches the cable endpoint');
}
for(const [a,b] of [[{id:'0',x:0,y:0},{id:'1',x:0,y:360}],[{id:'0',x:0,y:360},{id:'1',x:0,y:0}]]) {
  const cable=context.relationGraphCurve(a,b,null,{kind:'main_story'});
  assert.ok(Math.abs(Math.hypot(cable.ex-b.x,cable.ey-b.y)-26)<1e-9,'both directions attach to the cover ring');
  assert.ok(Math.abs(Math.hypot(cable.sx-a.x,cable.sy-a.y)-26)<1e-9,'source also touches its cover ring');
}
assert.ok(Math.hypot(sharedForward.point(.2).x-sharedReverse.point(.8).x,sharedForward.point(.2).y-sharedReverse.point(.8).y)<1e-9,'reverse relations use exactly the same cable');
const narrowEdge=context.relationGraphCurve({id:'0',x:88,y:-204,labelWidth:112,boxHeight:100},
  {id:'2',x:88,y:156,labelWidth:112,boxHeight:100},null,
  {kind:'side_story',limits:{width:320,height:580},obstacles:[{x:88,y:-24,labelWidth:112,boxHeight:100}]});
for(let t=0;t<=1;t+=.05)assert.ok(Math.abs(narrowEdge.point(t).x)<160,'tall small graph routes stay inside the viewport');
console.log('PASS target-end labels, shared reverse cable, facing attachment, and narrow edge routing');
{
  const pair=[{id:'a',groupId:'g',x:-100,y:0,labelWidth:80,boxHeight:124},{id:'b',groupId:'g',x:100,y:0,labelWidth:80,boxHeight:124}];
  const links=context.relationGraphSoftLinks(pair);
  pair[0].x=-440;pair[0].y=180;
  for(let i=0;i<40;i++)context.relationGraphSoftStep(pair,links,pair[0],{width:1600,height:1000});
  assert.ok(Math.hypot(pair[1].x-pair[0].x,pair[1].y-pair[0].y)<260,'dragged pair brings its companion along instead of leaving an excessively stretched membrane');
  pair[1].x=pair[0].x-120;pair[1].y=pair[0].y;
  context.relationGraphSoftStep(pair,links,pair[1],{width:1600,height:1000});
  assert.ok(pair[1].x<pair[0].x,'the second book can cross to the left of the first; initial ordering never locks drag direction');
  const above=context.relationGraphMemberEnvelope({x:0,y:0,labelWidth:80,boxHeight:124,captionAbove:true,captionTop:-120,captionOffset:25});
  assert.ok(above.y-above.ry<-120 && above.y+above.ry>24,'body collision envelope covers both above-caption rows and the cover');
  const actual=[{id:'a',x:-210,y:100,labelWidth:80,boxHeight:124},{id:'b',x:40,y:100,labelWidth:80,boxHeight:124}];
  const lagged=actual.map(n=>({...n,x:n.x+100}));
  const skin=context.relationGraphGroupBounds(null,lagged,[],false,actual);
  assert.ok(skin.x-skin.width/2<actual[0].x-40,'elastic lag may not leave the actual leftmost book outside its membrane');
  pair[0].x=-790;pair[1].x=-590;
  context.relationGraphSoftStep(pair,links,pair[0],{width:1000,height:800});
  const finalBody=context.relationGraphBodyLimits(pair);
  assert.ok(finalBody.left>=-488.001 && finalBody.right<=488.001,'post-spring pair correction keeps the entire body inside the viewport');
  const heldInBounds={x:-100,y:0,labelWidth:80,boxHeight:124},companionAtWall={x:100,y:700,labelWidth:80,boxHeight:124};
  context.relationGraphFitBody([heldInBounds,companionAtWall],heldInBounds,{width:1000,height:800});
  assert.equal(heldInBounds.y,0,'a companion at the bottom wall cannot push an in-bounds held work upwards');
  const ring=Array.from({length:6},(_,i)=>({id:String(i),groupId:'anthology',x:Math.cos(i*Math.PI/3)*180,y:Math.sin(i*Math.PI/3)*150,labelWidth:80,boxHeight:110}));
  const ringLinks=context.relationGraphSoftLinks(ring);
  for(const n of ring)n.x=n.x*.2+80;
  for(let i=0;i<100;i++)context.relationGraphSoftStep(ring,ringLinks,null,{width:1400,height:1000});
  assert.ok(Math.max(...ring.map(n=>n.x))-Math.min(...ring.map(n=>n.x))>230,'a six-book body retains width instead of collapsing into a tall column');
}
const outsideFan=context.relationGraphCurve({id:'0',x:180,y:-180,labelWidth:112,boxHeight:89},
  {id:'2',x:180,y:180,labelWidth:112,boxHeight:101},null,{kind:'side_story',limits:{width:824,height:580},
    obstacles:[{x:180,y:0,labelWidth:112,boxHeight:89}],spokes:[{a:{x:-180,y:0},b:{x:180,y:0}}]});
assert.ok(outsideFan.point(.5).x>180,'outer peer relation avoids crossing the current-work fan');
