export function parseRoute(hash,knownIds=[]) {
  const fallback={page:'discover',id:null,tab:'overview'};
  if(!hash || hash==='#/' || hash==='#') return {...fallback,page:'home'};
  const [path,query='']=String(hash).replace(/^#/,'').split('?');
  if(['/discover','/plan','/compare','/sources','/settings','/research','/knowledge'].includes(path)) return {...fallback,page:path.slice(1)};
  if(path.startsWith('/program/')) { let id; try{id=decodeURIComponent(path.slice(9));}catch{return fallback;} if(!knownIds.includes(id)) return fallback; const tab=new URLSearchParams(query).get('tab'); return {...fallback,page:'program',id,tab:['history','transfer'].includes(tab)?tab:'overview'}; }
  return fallback;
}
export function paginate(items,page=1,size=6) { const totalPages=Math.max(1,Math.ceil(items.length/size)); const current=Number.isInteger(page)&&page>0?Math.min(page,totalPages):1; return {items:items.slice((current-1)*size,current*size),page:current,totalPages,total:items.length}; }

export function nextTabIndex(key,index,length) {
  if(!Number.isInteger(index)||index<0||!Number.isInteger(length)||length<1) return -1;
  if(key==='Home') return 0;
  if(key==='End') return length-1;
  if(key==='ArrowRight') return (index+1)%length;
  if(key==='ArrowLeft') return (index-1+length)%length;
  return -1;
}
