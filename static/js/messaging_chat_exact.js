(function(){"use strict";
function all(selector,root){return Array.from((root||document).querySelectorAll(selector));}
function openPane(name){all("[data-mx-pane]").forEach(function(p){p.classList.toggle("is-active",p.dataset.mxPane===name);});all("[data-mx-open]").forEach(function(b){b.classList.toggle("is-active",b.dataset.mxOpen===name);});try{sessionStorage.setItem("pakivox-mx-pane",name);}catch(e){}if(name==="chat"){scrollBottom();}}
function scrollBottom(){var box=document.getElementById("chat-messages");if(box){box.scrollTop=box.scrollHeight;}}
document.addEventListener("DOMContentLoaded",function(){
var rows=all(".mx-conversation"),tabs=all("[data-mx-filter]"),search=document.getElementById("mxSearch"),active="all";
function filter(){var term=((search&&search.value)||"").toLowerCase().trim();rows.forEach(function(row){var visible=active==="all"||(active==="mine"&&row.dataset.mine==="1")||(active==="unassigned"&&row.dataset.assigned==="0")||(active==="unread"&&Number(row.dataset.pending)>0);if(visible&&term){visible=(row.dataset.search||"").toLowerCase().includes(term);}row.hidden=!visible;});}
tabs.forEach(function(tab){tab.addEventListener("click",function(){tabs.forEach(function(x){x.classList.remove("is-active");});tab.classList.add("is-active");active=tab.dataset.mxFilter;filter();});});if(search){search.addEventListener("input",filter);}
all("[data-mx-open]").forEach(function(button){button.addEventListener("click",function(){openPane(button.dataset.mxOpen);});});
all("[data-mx-reply]").forEach(function(button){button.addEventListener("click",function(){var area=document.querySelector("#mxReplyForm textarea");if(area){area.value=button.dataset.mxReply;area.focus();}});});
rows.forEach(function(row){row.addEventListener("click",function(){try{sessionStorage.setItem("pakivox-mx-pane","chat");}catch(e){}});});
if(window.matchMedia("(max-width: 980px)").matches){var saved="chat";try{saved=sessionStorage.getItem("pakivox-mx-pane")||"chat";}catch(e){}openPane(saved);}scrollBottom();
var refreshing=false,lastIds=all("[data-message-id]").map(function(x){return x.dataset.messageId;}).join("|");
window.setInterval(async function(){if(refreshing||document.hidden)return;refreshing=true;try{var response=await fetch(window.location.href,{credentials:"same-origin",headers:{"X-Requested-With":"XMLHttpRequest","Cache-Control":"no-cache"},cache:"no-store"});if(!response.ok)return;var html=await response.text(),doc=new DOMParser().parseFromString(html,"text/html"),fresh=doc.getElementById("chat-messages"),current=document.getElementById("chat-messages");if(!fresh||!current)return;var ids=all("[data-message-id]",fresh).map(function(x){return x.dataset.messageId;}).join("|");if(ids!==lastIds){var near=current.scrollHeight-current.scrollTop-current.clientHeight<120;current.innerHTML=fresh.innerHTML;lastIds=ids;if(near)scrollBottom();}}catch(e){}finally{refreshing=false;}},3000);
});
})();
