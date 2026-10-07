import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Natro's address and the device token come from local.properties, which stays out of git:
//   natro.server=ws://100.101.107.5:8700
//   natro.token=<NATRO_DEVICE_TOKEN from .env>
val local = Properties().apply {
    rootProject.file("local.properties").takeIf { it.exists() }?.inputStream()?.use { load(it) }
}

// The "OK Natro" wake word runs on the phone with sherpa-onnx (github.com/k2-fsa/sherpa-onnx): its Android
// library and the keyword-spotting model come from its GitHub releases, downloaded once (git-ignored).
val sherpaVersion = "1.13.8"
val sherpaAar = file("libs/sherpa-onnx-$sherpaVersion.aar")
val kwsModel = "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
val kwsFiles = listOf("tokens.txt", "encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx",
    "decoder-epoch-13-avg-2-chunk-8-left-64.onnx", "joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx")

fun download(url: String, to: File) {
    if (to.exists()) return
    to.parentFile.mkdirs()
    val partial = File(to.path + ".part")
    uri(url).toURL().openStream().use { input -> partial.outputStream().use { input.copyTo(it) } }
    partial.renameTo(to)
}

// The library must be there before Gradle reads the dependencies below.
download("https://github.com/k2-fsa/sherpa-onnx/releases/download/v$sherpaVersion/sherpa-onnx-$sherpaVersion.aar", sherpaAar)

val kwsArchive = layout.buildDirectory.file("downloads/$kwsModel.tar.bz2")
val downloadWakeWordModel = tasks.register("downloadWakeWordModel") {
    outputs.file(kwsArchive)
    doLast {
        download("https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/$kwsModel.tar.bz2",
            kwsArchive.get().asFile)
    }
}
val unpackWakeWordModel = tasks.register<Copy>("unpackWakeWordModel") {
    dependsOn(downloadWakeWordModel)
    from(tarTree(resources.bzip2(kwsArchive))) {
        include(kwsFiles.map { "*/$it" })
        eachFile { path = name }
    }
    includeEmptyDirs = false
    into(layout.projectDirectory.dir("src/main/assets/kws"))
}
tasks.named("preBuild") { dependsOn(unpackWakeWordModel) }

android {
    namespace = "dev.natro"
    compileSdk = 37

    defaultConfig {
        applicationId = "dev.natro"
        minSdk = 34
        targetSdk = 36
        versionCode = 1
        versionName = "0.1"
        buildConfigField("String", "NATRO_SERVER", "\"${local.getProperty("natro.server", "ws://100.101.107.5:8700")}\"")
        buildConfigField("String", "NATRO_TOKEN", "\"${local.getProperty("natro.token", "")}\"")
        // The Nothing Phone (2) is arm64; the other CPU types would only make the app bigger.
        ndk { abiFilters += "arm64-v8a" }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2026.09.00"))
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.11.0")
    implementation("androidx.core:core-ktx:1.19.1")
    implementation("com.squareup.okhttp3:okhttp:5.5.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
    implementation(files(sherpaAar))
    testImplementation("junit:junit:4.13.2")
}
