(function(){
  const btn=document.getElementById('watchAdBtn');
  const msg=document.getElementById('adMsg');
  let busy=false;

  // Monetag In-App Interstitial.
  // This is a normal ad format and does NOT grant ZIBSOL.
  // Monetag controls the frequency/capping/interval/session behavior.
  function startInAppInterstitial(){
    if(typeof window.show_11933619!=='function') return false;
    try{
      window.show_11933619({
        type:'inApp',
        inAppSettings:{
          frequency:2,
          capping:0.1,
          interval:30,
          timeout:5,
          everyPage:false
        }
      });
      return true;
    }catch(e){
      console.warn('Monetag in-app interstitial could not start:',e);
      return false;
    }
  }

  // The SDK can load asynchronously, so retry briefly until it is ready.
  function initInterstitial(){
    if(startInAppInterstitial()) return;
    let attempts=0;
    const timer=setInterval(function(){
      attempts++;
      if(startInAppInterstitial() || attempts>=20) clearInterval(timer);
    },500);
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',initInterstitial,{once:true});
  else initInterstitial();

  if(!btn) return;
  function text(v){if(msg)msg.textContent=v;}

  btn.addEventListener('click',async function(){
    if(busy)return;
    if(typeof window.show_11933619!=='function'){
      text('Ad service is still loading. Please try again in a moment.');
      return;
    }
    busy=true; btn.disabled=true; text('Opening rewarded ad…');
    try{
      await window.show_11933619('pop');
      text('Ad completed. Verifying reward…');
      const tg=window.Telegram.WebApp;
      const auth=tg.initData;
      const r=await fetch('/api/ads/reward',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'tma '+auth},cache:'no-store'});
      const d=await r.json().catch(()=>({}));
      if(!r.ok) throw new Error(d.detail||d.error||('HTTP '+r.status));
      const balance=document.getElementById('balance');
      if(balance) balance.textContent=Number(d.balance).toLocaleString()+' ZIBSOL';
      text('✅ +50 ZIBSOL added to your balance.');
    }catch(e){
      text(e.message||'The ad could not be completed.');
    }finally{
      busy=false; btn.disabled=false;
    }
  });
})();
