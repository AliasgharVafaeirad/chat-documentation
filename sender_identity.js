(rows, {selector, provider}) => {
  const root = window.__baleBackupBox;
  if (!root?.isConnected) throw new Error('Selected panel closed');
  const excluded = '.embedded-message,.EmbeddedMessage,.message-subheader,.forward-custom-shape-subheader,.forward-avatar,.forward-title-container,.via-sender,.WebPage,.Contact,.contact-card,.message-reaction,.reactions,[data-sentry-component="Preview"],[data-testid="quoted-message"],[data-testid="quoted-document"],[data-testid="quoted-media"],[data-testid="link-preview"],[data-testid="contact-message"],[data-testid="forwarded-header"]';
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const text = node => {
    if (!node) return '';
    const copy = node.cloneNode(true);
    copy.querySelectorAll('img').forEach(img => img.replaceWith(document.createTextNode(img.alt || '')));
    copy.querySelectorAll('.message-title-meta,.sender-boosts,.admin-title,.via-sender,svg,i').forEach(n => n.remove());
    return clean(copy.textContent);
  };
  const allowed = node => {
    if(!node || node.closest(excluded + ',.forward-title,.forwarded-message,.forward-header'))return false;
    const title=node.closest('.message-title');
    const marker=title?.querySelector('.forward-title,.forward-title-container');
    // Telegram includes an EMPTY forward-title-container on ordinary group
    // messages too. Only a populated marker identifies a forwarded header.
    return !marker || (!clean(marker.textContent) && marker.childElementCount===0);
  };
  const nodes = rows.map(row => root.querySelector(`[data-backup-token="${row.token}"]`));
  const account=window.__backupAccountIdentity;
  const selfIdentity=()=>{
    if(account?.provider===provider && account.id && account.name)
      return {name:account.name,id:account.id,role:'self',source:account.source};
    return {name:'من',role:'self',source:'outgoing_direction'};
  };
  const direct = nodes.map(el => {
    if (!el) return {};
    let role = el.matches('.own,.message-out') ? 'self' : 'participant';
    if (provider === 'telegram' && role === 'self') return selfIdentity();
    // WhatsApp's copy metadata belongs to the bubble, not the quoted message.
    const metadata = [...el.querySelectorAll('[data-pre-plain-text]')].find(allowed)?.getAttribute('data-pre-plain-text') || '';
    const parsed = metadata.match(/^\[[^\]]+\]\s*(.*):\s*$/);
    if (provider === 'whatsapp' && parsed && clean(parsed[1]))
      return {name: clean(parsed[1]), source: 'message_metadata', role};
    if (provider==='whatsapp') {
      const bubble=el.querySelector('[data-testid="msg-container"]') || el;
      const label=[...bubble.querySelectorAll('span[aria-label]')].find(node=>allowed(node) && !node.closest('[data-testid="msg-meta"],[data-testid="forwarded-header"],[data-testid="document-thumb"]') && /:\s*$/.test(node.getAttribute('aria-label') || ''))?.getAttribute('aria-label')?.replace(/:\s*$/,'').trim();
      const selfLabel=label && /^(you|me|شما|من|أنت|انت)$/i.test(label);
      if(el.matches('.message-out') || bubble.querySelector('[data-testid="tail-out"]') || selfLabel)return selfIdentity();
      if(label)return {name:label,role:'participant',source:'whatsapp_sender_accessibility_label'};
      if(bubble.querySelector('[data-testid="tail-in"]'))role='participant';
      else if(!el.matches('.message-in'))role='unknown';
    }
    let explicitId = el.getAttribute('data-sender-id') || el.getAttribute('data-author-id') || '';
    // Bale React props are read only from the message component, never app-wide state.
    if (provider === 'bale') {
      const key = Object.keys(el).find(k => k.startsWith('__reactFiber$'));
      let fiber = key ? el[key] : null;
      for (let depth = 0; fiber && depth < 25; depth++, fiber = fiber.return) {
        const p = fiber.memoizedProps;
        const m = p?.messageContainer || p?.messageContent;
        if (!m?.message) continue;
        if (typeof m.isOut === 'boolean') role = m.isOut ? 'self' : 'participant';
        const rawId = m.senderUid || m.senderId || m.message.senderUid || m.message.senderId || m.message.fromId || '';
        const sender = m.sender || m.fromUser || m.senderUser || m.message.sender;
        explicitId = clean(rawId || sender?.id || explicitId);
        if (sender && typeof sender === 'object') {
          const name = clean(sender.displayName || sender.fullName || [sender.firstName, sender.lastName].filter(Boolean).join(' '));
          if (name && (!rawId || !sender.id || String(rawId) === String(sender.id)))
            return {name, id: explicitId, role, source: 'message_props'};
        }
        const senderName=clean(m.senderName || m.authorName);
        if (senderName && explicitId) return {name:senderName,id:explicitId,role,source:'message_props'};
        if(explicitId) {
          // Read display profiles only when their UID equals THIS message's
          // senderUid. Quoted/forwarded profiles with another UID are ignored.
          for(const node of [el,...el.querySelectorAll('[data-sentry-component],img')].slice(0,80)) {
            let current=node[Object.keys(node).find(key=>key.startsWith('__reactFiber$'))];
            for(let level=0;current && level<8;level++,current=current.return) {
              const props=current.memoizedProps;
              for(const user of [props?.user,props?.sender,props?.senderUser,props?.userInfo,props?.profile,props?.peer]) {
                if(!user || typeof user!=='object')continue;
                const uid=clean(user.uid || user.id || user.userId);
                if(uid!==explicitId)continue;
                const name=clean(user.displayName || user.fullName || [user.firstName,user.lastName].filter(Boolean).join(' ') || user.name);
                if(name)return {name,id:explicitId,role,source:'sender_uid_profile'};
              }
            }
          }
        }
        break;
      }
    }
    if (role === 'self' && provider !== 'bale') return selfIdentity();
    for (const node of el.querySelectorAll('.sender-title,.sender-name,[data-testid="sender-name"],[data-testid="msg-author"],[data-sender-name],.message-title,[data-sentry-component="SenderName"],[data-sentry-component="MessageSender"],[data-sentry-component="AuthorName"]')) {
      if (!allowed(node)) continue;
      const name = clean(node.getAttribute('data-sender-name')) || text(node);
      if (name) return {name, id: explicitId, role, source: 'message_label'};
    }
    for (const avatar of el.querySelectorAll('.Avatar[data-peer-id],.avatar[data-user-id]')) {
      if (!allowed(avatar)) continue;
      if (avatar.closest('.message-content') && !avatar.matches('.sender-avatar,.message-sender-avatar')) continue;
      const name = clean(avatar.getAttribute('aria-label')) || clean(avatar.querySelector('img[alt]')?.alt);
      if (name) return {name, id: avatar.getAttribute('data-peer-id') || avatar.getAttribute('data-user-id'), role, source: 'sender_avatar'};
    }
    if (role === 'self') return selfIdentity();
    return {id: explicitId, role};
  });
  // Telegram suppresses repeated names inside explicit same-sender groups.
  // Never carry a name across first/last group, date parent, or direction boundaries.
  if (provider === 'telegram') {
    let group = [];
    const flush = () => {
      const named = group.filter(index => direct[index].name);
      const unique = [...new Set(named.map(index => direct[index].name))];
      const ids = new Set(group.map(index => direct[index].id).filter(Boolean));
      if (unique.length === 1 && ids.size <= 1) {
        const sourceIndex = named[0];
        for (const index of group) if (!direct[index].name)
          direct[index] = {...direct[index], name: unique[0], id: direct[sourceIndex].id || direct[index].id,
            source: 'message_group', source_message_id: rows[sourceIndex].id};
      }
      group = [];
    };
    for (let index = 0; index < nodes.length; index++) {
      const el = nodes[index];
      const previous = nodes[index - 1];
      if (!el || !el.matches('.first-in-group,.last-in-group') && !el.classList.contains('message-list-item')) {flush(); continue;}
      if (el.classList.contains('first-in-group') || previous?.classList.contains('last-in-group') ||
          previous && (el.parentElement !== previous.parentElement || direct[index].role !== direct[index - 1].role)) flush();
      group.push(index);
      if (el.classList.contains('last-in-group')) flush();
    }
    flush();
    // Positive peer ID identifies a one-to-one Telegram chat, not a group title.
    const header = document.querySelector('#MiddleColumn .MiddleHeader .ChatInfo');
    const peerId = header?.querySelector('[data-peer-id]')?.getAttribute('data-peer-id');
    const peerName = text(header?.querySelector('.fullName'));
    if (peerId && /^\d+$/.test(peerId) && Number(peerId) > 0 && peerName) {
      for (const item of direct) if (item.role !== 'self')
        Object.assign(item, {name: peerName, id: peerId, source: 'private_chat_header'});
    }
  }
  // Recover a suppressed label only by an explicit sender ID in this scan.
  // Conflicting labels for an ID are deliberately left unresolved.
  const byId = new Map();
  for (const identity of direct) if (identity.id && identity.name && identity.role !== 'self') {
    if (!byId.has(identity.id)) byId.set(identity.id, new Set());
    byId.get(identity.id).add(identity.name);
  }
  for (const identity of direct) if (!identity.name && identity.id && identity.role !== 'self') {
    const names=byId.get(identity.id);
    if (names?.size===1) Object.assign(identity,{name:[...names][0],source:'explicit_sender_id'});
  }
  return rows.map((row, index) => {
    const identity = direct[index];
    const forwarded = nodes[index]?.querySelector('.message-title .forward-title')?.closest('.message-title')?.querySelector('.sender-title') || nodes[index]?.querySelector('.forward-title-container .sender-title,.forward-header .sender-title');
    return {...row, sender: identity.name || 'نامشخص', sender_id: identity.id || '',
      sender_role: identity.role || 'unknown', sender_source: identity.source || 'unavailable',
      sender_status: identity.source==='outgoing_direction' ? 'role_only' : (identity.name ? 'displayed_name' : 'unknown'),
      sender_source_message_id: identity.source_message_id || row.id,
      forwarded_from: text(forwarded)};
  });
}
