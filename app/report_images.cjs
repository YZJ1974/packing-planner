const fs=require('fs');
const [manifestPath,modulePath]=process.argv.slice(2);
const sharp=require(require.resolve('sharp',{paths:[modulePath]}));
(async()=>{const jobs=JSON.parse(fs.readFileSync(manifestPath,'utf8'));for(const j of jobs){await sharp(fs.readFileSync(j.svg),{density:192}).resize({width:2400}).trim().extend({top:12,bottom:12,left:12,right:12,background:'#ffffff'}).png({compressionLevel:6}).toFile(j.png);}})().catch(e=>{console.error(e.message);process.exit(1)});
