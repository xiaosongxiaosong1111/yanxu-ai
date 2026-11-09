function picStoryLoad(columnId,pageNo) {
  let url = ''
  var previewId=$('#templateTag').val().replace('preview','');
  var channels = $('#siteDirname').val()
  if(pageNo == 0) {
    url = '/site/template/preview/channels/' + channels + '/columns/'+ columnId +'?previewId=' + previewId + '&fileName=column-img-list'
  }else{
  	url = '/site/template/preview/channels/' + channels + '/columns/'+ columnId +'?previewId=' + previewId + '&fileName=column-img-list&pageNo=' + pageNo
  }
  window.location.href = url;
}

function serviceLoad(columnId, fileName, pageNo, keywords) {
  $('#navs a').removeClass('active');
  $('#' + fileName + '_page').addClass('active');
  searchStory(columnId, fileName, pageNo, keywords);

}
function getColumnDataList() {
  var columnId = $('#getColumnId').val();
  let url = 'https://center.hn0746.com/site/template/preview/channels/yzxww/columns/'+ columnId +'?previewId='+ $('#templateTag').val().replace('preview', '') +'&fileName=column-lm-list&pageNo=1&id='+ columnId
  window.location.href = url;
}
function getUrlValue(kValue) {
  var url = decodeURIComponent(window.location.href);  //当前页面的地址
  var reg = /([^?&=]+)=([^?&=]+)/g, obj = {}, str = url;
  url.replace(reg, function () {
    obj[arguments[1]] = arguments[2];
  })
  for (var keyName in obj) {
    if (keyName == kValue) {
      return obj[keyName];
      // break;
    }
  }
}
function tabLoad(id) {
  var columnId = $('#columnId').val();
  var fileName = $('#fileName').val();
  var keywords = $('#keywords').val();
  $('#navs a').removeClass('active');
  $('#' + fileName + '_page').addClass('active');
  var previewId = $('#templateTag').val().replace('preview/', '');
  var url = '/site/template/preview/channels/' + $('#siteDirname').val() + '/columns/' + columnId
  + '?previewId=' + previewId + '&fileName=' + fileName
  + '&pageNo=1' + '&id=' + id;
  window.location.href = url;
}

function searchBychannels() {
  var keywords = $('#searchWord').val();
  if (keywords && keywords.length > 0) {
    searchStory('5d22f24ce4b09067cf75235c', 'column-search', 1, keywords);
  }
}
function resizeIframeHeight (iframeEl){
  const iframeWindow = iframeEl.contentWindow || iframeEl.contentDocument.parentWindow
  if (iframeWindow.document.body) {
    // 获取主容器高度，iframe内的DIV容器高度是会自己撑开的
    var height = iframeWindow.document.querySelector('#app').offsetHeight
    height = height > 1000 ? 1000 : height;
    iframeEl.height = (height+65) + 'px'
  }
}
function handleIframeHeight(){
  const iframeEl = document.querySelector('#iframe')
  resizeIframeHeight(iframeEl)
}

function showStory(ele, fileName) {
  let storyId = $(ele).attr('data-id');
  let url = '/content/' + storyId + '.html';
  window.location.href = url;
}
function goQuestion () {
  window.location.href = '/content/67595383cf1e66a18c75f36a.html'
}
function showColumn(ele) {
  let columnUrl = $(ele).attr('data-url')
  var templateTag = $('#templateTag').val();
  var url = '';
  if (templateTag.indexOf('preview') > -1) {
    var previewId = $('#templateTag').val().replace('preview', '');
    url = '/template/preview/channels/' + $('#siteDirname').val() + '/stories/' + storyId
      + '?previewId=' + previewId + '&fileName=content-' + fileName;
  } else {
    url = '/' + columnUrl;
  }
  window.location.href = url;
}

function imgSearch(ele,type) {
  let typeVal = $('#searchKeywordsCars').val()
  let val = $('#searchKeywords').val();
  console.log("type:11111111",type,typeVal)
  if(type == 'searchKeywords'){
    val = $('#searchKeywords').val();
  }else if(type == 'searchKeywords1'){
    val = $('#searchKeywords1').val();
  }
  let host = window.location.host;
  if (val == '') {
    alert('请输入搜索内容')
    return;
  }
  if(typeVal == '中国搜索') {
    window.open('https://www.chinaso.com/newssearch/all/allResults?q='+val)
    return
  }
  window.open('/site/search?query='+val)
}

function imgSearchs(ele,type) {

  window.open('/site/column-lm-list2?query=1');
}

function dongaoSearch() {
  let val = $('.search-input').val()
  let host = window.location.host;
  if (val == '') {
    alert('请输入搜索内容')
    return;
  }
  window.open('/site/search?query=' + val);
}

$(document).keydown(function (event) {//回车搜索
  var e = event || window.event || arguments.callee.caller.arguments[0];
  if (e && e.keyCode == 13) { // enter 键
    //imgSearch()
    // searchBychannels();
  }
})
function serviceLoads(columnId, fileName, pageNo, keywords) {
  $('.news_nav li').removeClass('active');
  $('#' + fileName + '_page').addClass('active');
  searchStorys(columnId, fileName, pageNo, keywords);

}
function searchStorys(columnId, fileName, pageNo, keywords) {
  var templateTag = $('#templateTag').val();
  var url = '';
  if (templateTag.indexOf('preview') > -1) {
    var previewId = $('#templateTag').val().replace('preview', '');
    url = '/template/preview/channels/' + $('#siteDirname').val() + '/columns/' + columnId
      + '?previewId=' + previewId + '&fileName=' + fileName
      + '&pageNo=' + (pageNo ? pageNo : 1)
      + '&keywords=' + keywords;
  } else {
    url = '/channels/' + $('#siteDirname').val() + '/' + columnId
      + '/page_' + (pageNo ? pageNo : 1)
      + '.html?keywords=' + keywords;
  }
  window.location.href = url;
  $('html').animate({ scrollTop: 0 }, 0);
}
setTimeout(function () {
  $('.rightFirNews').on('click', function () {
    var host = window.location.host;
    var hostName = host.split('.')[0];
    let id = $(this).attr('id');
    let type = $(this).attr('type');
    if (type == 2) {
      window.open('http://' + hostName + '.wengetech.com:28086/xhs_al/xhs/getInfoDetailByID2?path=/page/xhs_al/detail.html&app_info_id=13&auto_id=' + id)
    } else {
      window.open('http://hongqi.wengetech.com:28086/xhs_al/xhs/getInfoDetailByID?app_info_id=13&auto_id=' + id)
    }
  })
}, 1000)

$(function () {
  var yiny = $("#yinyue").attr("href", "https://www.meituan.com/cate/175253913?utm_term=AiphoneBgroupC10.0.800DqqEpoiG0000000000000B291CE5D53CF4A6BB91E45549F81CD23A15550131128241468320190725142604652&utm_source=appshare&utm_medium=iOSweb&utm_fromapp=qq&utm_sharesource=poi&lch=appshare_jyiamq6xzccx")
  var shuf = $("#shufa").attr("href", "https://www.meituan.com/cate/181531658?utm_term=AiphoneBgroupC10.0.800DqqEpoiGF93F641A513D193361769FB4FCEAF518C617EABD4D648D6743E422243CEF50A620190725142838080&utm_source=appshare&utm_medium=iOSweb&utm_fromapp=qq&utm_sharesource=poi&lch=appshare_jyiaq0s57ho5")
  var zhap = $("#zhapian").attr("href", "https://fw.kaipuyun.cn/servicePage?serviceName=unhealthySwindlePhone&cityID=350000&clientID=123qwe&localFrom=xinhuashe_app")
  var ger = $("#geren").attr("href", "https://fw.kaipuyun.cn/servicePage?serviceName=unhealthyPersonInfo&cityID=350000&clientID=123qwe&localFrom=xinhuashe_app")
  var duanx = $("#duanxin").attr("href", "https://fw.kaipuyun.cn/servicePage?serviceName=unhealthyReportMessage&cityID=350000&clientID=123qwe&localFrom=xinhuashe_app")
  // 列表展示
  $('.btn_left').click(function () {
    $(this).addClass('cur')
    $('.btn_right').removeClass('cur')
    $('.columnList .con').removeClass('kapian')
    $('.columnList .con').find('.brief').show()
    $('.columnList .con').find('.columnListTitle').show()
    $('.columnList .con').find('.titlekapain').hide()
  })
  // 卡片展示
  $('.btn_right').click(function () {
    $(this).addClass('cur')
    $('.btn_left').removeClass('cur')
    $('.columnList .con').addClass('kapian')
    $('.columnList .con').find('.brief').hide()
    $('.columnList .con').find('.columnListTitle').hide()
    $('.columnList .con').find('.titlekapain').show()
  })
})

function targetLink(that) {
  window.open($(that).attr("data-id"))
}

function targetUrl(obj) {
  if ($(obj).attr("data-id")) {
    window.open('http://hongqiplus.wengegroup.com/site/' + $(obj).attr("data-id"))
  }
}
function getPreviewUrl(fileName) {
  var previewId = $('#templateTag').val().replace('preview/', '');
  var url = '/site/template/preview/channels/' + $('#siteDirname').val()
  + '?previewId=' + previewId + '&fileName=' + fileName;
  return url;
}
function handleAllJump(tarUrl, localMap, prodMap) {
  // if(!sessionStorage.getItem('token')){
  //   return
  // }
  if (/127.0.0.1/.test(window.location.href)) {
    window.location.href = `${localMap[tarUrl]}.html?page=${tarUrl}`
  } else if (/\/cityMini\/site\/template\/preview/.test(window.location.href)) {
    window.location.href = getPreviewUrl(localMap[tarUrl])
  } else {
    window.location.href = prodMap[tarUrl]
  }
}
function checkIsLogin(){
  let curUserToken = sessionStorage.getItem('token');
  if(!curUserToken){ // 跳转登录页
    window.location.href = 'http://qscloudhongqi.wengegroup.com/site/myLogin'
  }
}

function pageJump(type,url,murl,params) { // 栏目页面跳转
  if (/127.0.0.1/.test(window.location.href)) {
    if(type==='open') {
      window.open(`${url}.html?` + params);
    }else {
      window.location.href = `${url}.html?` + params;
    }
  } else if (/\/cityMini\/site\/template\/preview/.test(window.location.href)) {
    if(type==='open') {
      window.open(previewUrl(url,params));
    }else {
      window.location.href = previewUrl(url,params);
    }
  } else {
    if(type==='open') {
      window.open(murl + '?' + params);
    }else {
      window.location.href = murl + '?' + params;
    }
  }
}
function previewUrl(fileName,params='') {
  var previewId = $('#templateTag').val().replace('preview/', '');
  var url = '/site/template/preview/channels/' + $('#siteDirname').val()
  + '?previewId=' + previewId + '&fileName=' + fileName + params;
  return url;
}
function  newServiceLoad(parentColumnId,pageNo){
  var columnId=$('#columnId').val();
  var fileName=$('#fileName').val();
  var keywords=$('#keywords').val();
  if(!parentColumnId){
    parentColumnId = $('#parentColumnId').val()
  }
  if(!fileName) {
    fileName = 'column-list'
  }
  $('#navs a').removeClass('active');
  $('#'+fileName+'_page').addClass('active');
  searchStory(parentColumnId,columnId,fileName,pageNo,keywords);
}


function searchStory(parentColumnId,columnId,fileName,pageNo,keywords){
  var templateTag=$('#templateTag').val();
  var url='';
  if(templateTag.indexOf('preview')>-1){
    var previewId=$('#templateTag').val().replace('preview','');
    if(parentColumnId){
      url='/site/template/preview/channels/'+$('#siteDirname').val()+'/columns/'+parentColumnId+'/'+columnId
        +'?previewId='+previewId+'&fileName='+fileName
        +'&pageNo='+(pageNo?pageNo:1);
    }else{
      url='/site/template/preview/channels/'+$('#siteDirname').val()+'/columns/'+columnId
        +'?previewId='+previewId+'&fileName='+fileName
        +'&pageNo='+(pageNo?pageNo:1);
    }
  }else{
    if(parentColumnId){
      if(keywords) {
        url='/site/channels/'+$('#siteDirname').val()+'/'+parentColumnId+'/'+columnId
          +'/page_'+(pageNo?pageNo:1)
          +'.html?keywords='+keywords;
      }else {
        url='/site/channels/'+$('#siteDirname').val()+'/'+parentColumnId+'/'+columnId
          +'/page_'+(pageNo?pageNo:1)
          +'.html';
      }
      
    }else{
      if(keywords) {
        url='/site/channels/'+$('#siteDirname').val()+'/'+columnId
          +'/page_'+(pageNo?pageNo:1)
          +'.html?keywords='+keywords;
      }else {
        url='/site/channels/'+$('#siteDirname').val()+'/'+columnId
          +'/page_'+(pageNo?pageNo:1)
          +'.html';
      }
    }
  }
  window.location.href = url;
}

	//分享到QQ
	function shareQQ(title, url, picurl) {
        // 分享的标题
        title = encodeURIComponent(title || document.title);
        // 分享内容链接
        url = encodeURIComponent(url || window.location.href);
        // 分享图片的路径，多张图片以＂|＂隔开，可选参数
        picurl = encodeURIComponent(picurl || '');
        var shareqqzonestring = 
            'http://connect.qq.com/widget/shareqq/index.html?title=' + title 
            + '&url=' + url 
            + '&pics=' + picurl;
        // 在新窗口中打开
        window.open(shareqqzonestring, '_blank');
    }
//分享到新浪微博
function shareWB(title, url, picurl) {
    // 分享的标题
    title = encodeURIComponent(title || document.title);
    // 分享内容链接
    url = encodeURIComponent(url || window.location.href);
    // 分享图片的路径，多张图片以＂|＂隔开，可选参数
    picurl = encodeURIComponent(picurl || '');
    var sharesinastring =
        'http://service.weibo.com/share/share.php?title=' + title 
        + '&url=' + url 
        + '&content=utf-8&sourceUrl=' + url 
        + '&pic=' + picurl;
    // 在新窗口中打开
    window.open(sharesinastring, '_blank');
}
//分享到QQ空间
function shareQQKJ(title, url, picurl, content) {
    // 分享的标题
    title = encodeURIComponent(title || document.title);
    // 分享内容链接
    url = encodeURIComponent(url || window.location.href);
    // 分享图片的路径，多张图片以＂|＂隔开，可选参数
    picurl = encodeURIComponent(picurl || '');
    // 分享描述
    content = encodeURIComponent(content || '');
    var shareqqzonestring = 
        'http://sns.qzone.qq.com/cgi-bin/qzshare/cgi_qzshare_onekey?url=' + url
        + '&title=' + title
        + '&desc='
        + '&summary='
        + '&site=' + title
        + '&pics=' + picurl;
    // 在新窗口中打开
    window.open(shareqqzonestring, '_blank');
}

