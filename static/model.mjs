export function toggleSaved(ids,id) { return ids.includes(id) ? ids.filter(value=>value!==id) : [...ids,id]; }
export function toggleCompare(ids,id,limit=3) { return ids.includes(id) ? ids.filter(value=>value!==id) : ids.length<limit ? [...ids,id] : [...ids]; }
export function latestLine(program) { return (program.scoreLines || []).reduce((latest,row)=>!latest || row.year>latest.year ? row : latest,null); }
