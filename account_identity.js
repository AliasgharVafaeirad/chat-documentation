async provider => {
  // Return only display identity; never export session keys or the account object.
  const clean=value=>String(value || '').replace(/\s+/g,' ').trim();
  const finish=(id,name,source,slot)=>({provider,id:clean(id),name:clean(name),source,slot});
  if(provider!=='telegram')return finish('','','message_metadata_only','');
  const slot=Number(new URL(location.href).searchParams.get('account') || 1);
  if(!Number.isInteger(slot) || slot<1 || slot>6)return finish('','','unavailable','');
  let account={};
  try {account=JSON.parse(localStorage.getItem('account'+slot) || '{}') || {};} catch {}
  let id=clean(account.userId);
  if(!id && slot===1) {try {id=clean(JSON.parse(localStorage.getItem('user_auth') || '{}')?.id);} catch {}}
  const name=clean([account.firstName,account.lastName].filter(Boolean).join(' '));
  if(id && name)return finish(id,name,'telegram_active_account',slot);
  // Legacy installations may retain the display state separately.
  const cacheKey='tt-global-state'+(slot===1?'':'_'+slot);
  const fromState=state=>{
    const current=clean(state?.currentUserId);
    if(!id || !current || current!==id)return null;
    const user=state?.users?.byId?.[current];
    if(!user || clean(user.id)!==current)return null;
    const label=clean([user.firstName,user.lastName].filter(Boolean).join(' '));
    return label?finish(current,label,'telegram_current_user_state',slot):null;
  };
  try {
    const found=fromState(JSON.parse(localStorage.getItem(cacheKey) || 'null'));
    if(found)return found;
  } catch {}
  try {
    const databases=await indexedDB.databases();
    if(databases.some(db=>db.name==='tt-data')) {
      const state=await new Promise(resolve=>{
        let db;const timer=setTimeout(()=>{db?.close();resolve(null);},2000);
        const request=indexedDB.open('tt-data');
        request.onerror=()=>{clearTimeout(timer);resolve(null);};
        request.onupgradeneeded=()=>{request.transaction.abort();clearTimeout(timer);resolve(null);};
        request.onsuccess=()=>{
          db=request.result;
          if(!db.objectStoreNames.contains('store')){db.close();clearTimeout(timer);resolve(null);return;}
          const query=db.transaction('store','readonly').objectStore('store').get(cacheKey);
          query.onsuccess=()=>{db.close();clearTimeout(timer);resolve(query.result);};
          query.onerror=()=>{db.close();clearTimeout(timer);resolve(null);};
        };
      });
      const found=fromState(state);if(found)return found;
    }
  } catch {}
  return finish(id,'','unavailable',slot);
}
