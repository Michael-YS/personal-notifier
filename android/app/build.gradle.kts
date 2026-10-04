plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.kapt")
    id("com.google.gms.google-services") apply false
}
val buildCheck = providers.gradleProperty("buildCheck").orNull == "true"
if (!buildCheck) apply(plugin = "com.google.gms.google-services")
android {
    namespace = "dev.personalnotify"
    compileSdk = 35
    defaultConfig {
        applicationId = "dev.personalnotify"
        minSdk = 26
        targetSdk = 35
        versionCode = 2
        versionName = "0.2.0"
        if (buildCheck) {
            applicationIdSuffix = ".buildcheck"
            versionNameSuffix = "-buildcheck"
        }
        resValue("string", "app_name", if (buildCheck) "Personal Notify (build check)" else "Personal Notify")
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildTypes { release { isMinifyEnabled = false } }
}
kapt { arguments { arg("room.schemaLocation", "$projectDir/schemas") } }
dependencies {
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    implementation("androidx.fragment:fragment:1.8.5")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation(platform("com.google.firebase:firebase-bom:33.16.0"))
    implementation("com.google.firebase:firebase-messaging")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    kapt("androidx.room:room-compiler:2.6.1")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-play-services:1.8.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test:core:1.6.1")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
}
