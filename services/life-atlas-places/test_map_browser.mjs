// Headless only. Reuses an installed Playwright/Chromium, never a live user profile.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const args = process.argv.slice(2);
const option = key => {const i = args.indexOf(key); return i < 0 ? null : args[i+1];};
let url = process.env.TRAVEL_MAP_TEST_URL;
let server;
const fixture = option('--fixture');
const screenshot = option('--screenshot');
if (fixture) {
  const body = await fs.readFile(fixture);
  assert(body.includes(Buffer.from('Synthetic data only')), 'browser fixture must be explicitly synthetic');
  server = http.createServer((req,res) => {
    if (req.url === '/favicon.ico') {res.writeHead(204); res.end(); return;}
    res.writeHead(200, {'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'}); res.end(body);
  });
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  url = `http://127.0.0.1:${server.address().port}/`;
}
assert(url && ['127.0.0.1','localhost'].includes(new URL(url).hostname), 'only local map tests are allowed');
assert(!screenshot || fixture, 'never save a screenshot of private report data');
const origin = new URL(url).origin;
const runtime = await fs.mkdtemp(path.join(process.env.XDG_RUNTIME_DIR || os.tmpdir(),'travel-map-test-'));
const previousTmp = process.env.TMPDIR;
process.env.TMPDIR = runtime;
let browser;
try {
  browser = await chromium.launch({...(process.env.MAP_TEST_BROWSER ? {executablePath: process.env.MAP_TEST_BROWSER} : {}),
    headless:true,args:['--no-sandbox','--disable-gpu','--disable-background-networking','--disable-component-update','--no-first-run','--no-default-browser-check']});
  const page = await browser.newPage({viewport:{width:1365,height:900}});
  const errors = [], external = [];
  page.on('pageerror',e => errors.push(e.message));
  page.on('console',message => {if (message.type()==='error' && /Content Security Policy|Refused to execute|Refused to load/i.test(message.text())) errors.push('CSP violation');});
  await page.route('**/*',route => {
    const request = route.request().url();
    if (!request.startsWith(origin+'/') && !request.startsWith('data:')) {external.push(new URL(request).origin);return route.abort();}
    return route.continue();
  });
  await page.goto(url,{waitUntil:'load'});
  await page.waitForFunction(() => !!window.travelMap?.map.getZoom(),undefined,{timeout:15000});
  assert.deepEqual(errors,[],'page JavaScript failed');
  assert.match(await page.locator('#overview').innerText(),/Historical trip/,'trip must appear before GPS');
  const mapBox = await page.locator('#map').boundingBox();
  assert(mapBox.width>500 && mapBox.height>350 && mapBox.y<300,'map must occupy the first viewport');
  const tripGroups = await page.locator('.place-card').count();
  assert(tripGroups>0,'trip needs named cards');
  assert.equal(await page.locator('.place-card > details[open]').count(),0,'source details must begin collapsed');
  const visibleMarkers = () => page.evaluate(() => {
    const map = document.getElementById('map').getBoundingClientRect();
    return [...document.querySelectorAll('#map .place-marker,#map .marker-cluster')].filter(node => {
      const r=node.getBoundingClientRect(); return r.width>0 && r.height>0 && r.left+r.width/2>map.left && r.left+r.width/2<map.right && r.top+r.height/2>map.top && r.top+r.height/2<map.bottom;
    }).length;
  });
  await page.waitForTimeout(400);
  assert((await visibleMarkers())>0,'first viewport must contain painted place/cluster markers');
  if (screenshot) await page.screenshot({path:screenshot,fullPage:false});
  if (fixture) {
    assert((await page.locator('.marker-cluster').count())>0,'nearby distinct markers must cluster visually');
    const previous = await page.evaluate(() => window.travelMap.map.getZoom());
    await page.locator('.marker-cluster').first().click();
    await page.waitForTimeout(350);
    assert(await page.evaluate(previous => window.travelMap.map.getZoom()>previous || (window.travelMap.markers._spiderfied?.getAllChildMarkers().length || 0)>=2,previous),'cluster click must zoom or separate overlapping markers');
    await page.evaluate(() => {const g=window.travelMap.visibleGroups[0];window.travelMap.map.setView([g.latitude,g.longitude],19,{animate:false});});
    await page.waitForTimeout(150);
    assert((await page.locator('.marker-cluster').count())>0,'overlapping markers need a cluster even at maximum zoom');
    await page.locator('.marker-cluster').first().click();
    await page.waitForTimeout(350);
    const spiderCount=await page.evaluate(() => window.travelMap.markers._spiderfied?.getAllChildMarkers().length || 0);
    assert(spiderCount>=2,'identical-coordinate markers must spiderfy at zoom 19');
    await page.locator('.place-marker').nth(0).click();
    const firstSelected=await page.locator('.place-card.selected').getAttribute('data-id');
    await page.locator('.place-marker').nth(1).click();
    const secondSelected=await page.locator('.place-card.selected').getAttribute('data-id');
    assert.notEqual(firstSelected,secondSelected,'both overlapping places must remain selectable');
    await page.locator('#fit').click();
  }
  const days = await page.locator('#day option').evaluateAll(options => options.map(o=>o.value));
  assert(days.length>1,'day selector missing');
  await page.selectOption('#day',days[1]);
  assert((await page.locator('.place-card').count())<=tripGroups);
  if (fixture) assert.match(await page.locator('.segment-summary').first().innerText(),/^1 source segment/,'day filter must filter segments, not just markers');
  await page.selectOption('#day','all');
  const beforeZoom = await page.evaluate(() => window.travelMap.map.getZoom());
  await page.locator('.leaflet-control-zoom-in').click();
  await page.waitForTimeout(350);
  assert((await page.evaluate(() => window.travelMap.map.getZoom()))>beforeZoom,'zoom control did not work');
  await page.locator('#gps').click();
  const gpsGroups = await page.locator('.place-card').count();
  if (option('--expected-gps-groups')) assert.equal(gpsGroups,Number(option('--expected-gps-groups')),'unexpected GPS group count');
  assert(gpsGroups>0 && gpsGroups<tripGroups+1,'GPS needs grouped areas, not a raw record dump');
  await page.locator('.place-title').first().click();
  assert.equal(await page.locator('.place-card.selected').count(),1,'card did not select map marker');
  await page.locator('.place-card > details > summary').first().click();
  assert((await page.locator('.recorded-times li').first().innerText()).length>10,'recorded segments not accessible');
  await page.setViewportSize({width:390,height:844});
  await page.locator('#history').click();
  await page.waitForTimeout(300);
  assert(await page.evaluate(() => document.documentElement.scrollWidth<=window.innerWidth+1),'mobile horizontal overflow');
  const mobileMap = await page.locator('#map').boundingBox();
  assert(mobileMap.height>=300 && mobileMap.width<=390,'mobile map layout failed');
  assert((await visibleMarkers())>0,'mobile viewport must contain place/cluster markers');
  if (screenshot) await page.screenshot({path:screenshot.replace(/\.png$/,'.mobile.png'),fullPage:false});
  const contextLayers=await page.evaluate(() => Object.values(window.travelMap.map._layers).filter(layer => layer.feature?.properties?.role).length);
  assert(contextLayers>0,'map context must render, not only a blank background');
  assert.deepEqual(errors,[],'interactive controls caused errors');
  assert.deepEqual(external,[],'map attempted an external request');
  console.log(JSON.stringify({headless:true,tripGroups,gpsGroups,mapFirstViewport:true,
    dayFilter:true,zoom:true,cardSelection:true,sourceDetails:true,mobileLayout:true,contextLayers,externalRequests:0,pageErrors:0}));
} catch(error) {
  const message = String(error.stack || error).split(url).join('[local private report]');
  console.error(message); process.exitCode=1;
} finally {
  if (browser) await browser.close();
  if (server) await new Promise(resolve => server.close(resolve));
  if (previousTmp === undefined) delete process.env.TMPDIR; else process.env.TMPDIR=previousTmp;
  await fs.rm(runtime,{recursive:true,force:true});
}
