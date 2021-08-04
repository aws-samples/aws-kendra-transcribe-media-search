// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import React from "react";
import Search from "./search/Search";
import { facetConfiguration } from "./search/configuration";
import S3 from 'aws-sdk/clients/s3';
import AWS from 'aws-sdk';
import aws_exports from './aws-exports';
import Kendra from 'aws-sdk/clients/kendra';
import Auth from '@aws-amplify/auth';
import { AuthState } from '@aws-amplify/ui-components';
import searchlogo from './searchConsoleArt.svg'

import "./App.css";

const indexId = process.env.REACT_APP_INDEX_ID!;
const region = process.env.REACT_APP_REGION!;
const role_arn = process.env.REACT_APP_ROLE_ARN!;

interface AppState {
  infraReady: boolean;
  loginScreen: boolean;
  authUser: boolean;
  kendra?: Kendra;
  s3?: S3;
  user?: string;
  accessToken?: string;
}

class App extends React.Component<string[], AppState> {
  constructor(props: string[]) {
    super(props);
    this.state = {
      infraReady: false,
      loginScreen: true,
      authUser: true,
      kendra: undefined,
      s3: undefined,
      user: undefined,
      accessToken: undefined
    };
  }
  
  handleClick = async () => {
    this.setState({authUser: !this.state.authUser});
  }
  
  authChangeState = async (nextAuthState: AuthState) => {
    try {
      let user = await Auth.currentAuthenticatedUser();
      //accessToken contains all the information about username, groupname etc.
      let accessToken = user.signInUserSession.accessToken.jwtToken;
      if (nextAuthState === AuthState.SignedIn){
        this.setState({loginScreen:false, authUser: true, user: user ? user!.username : undefined, accessToken: accessToken ? accessToken : undefined});
      } else {
        this.setState({loginScreen:true, authUser: true, user: user ? user!.username : undefined, accessToken: accessToken ? accessToken : undefined});
      }
    } catch {
      console.log('currentAuthenticatedUser Exception');
      if (nextAuthState === AuthState.SignedIn){
        this.setState({loginScreen:false, authUser: true, user: undefined, accessToken: undefined});
      } else {
        this.setState({loginScreen:true, authUser: true, user: undefined, accessToken: undefined});
      }
    }
  }
 
  setInfra(accessKeyId:string, secretAccessKey:string, sessionToken:string) {
    let sts = new AWS.STS({
      accessKeyId: accessKeyId,
      secretAccessKey: secretAccessKey,
      sessionToken: sessionToken,
      region: region
    });
    let sts_params = {
      RoleArn: role_arn,
      RoleSessionName:"wapp"+ Date.now(),
      DurationSeconds:3600
    };
    //Make a call to assume the role to access Kendra and corresponding S3 objects
    sts.assumeRole(sts_params, (err, data) => {
      if (err) console.log("sts_assumerole Error:", err);
      else {
        let kendra = new Kendra({
          accessKeyId: data.Credentials!.AccessKeyId,
          secretAccessKey: data.Credentials!.SecretAccessKey,
          sessionToken: data.Credentials!.SessionToken,
          region: region
        }); 
        //S3 is required to get signed URLs for S3 objects
        let s3 = new S3({
          accessKeyId: data.Credentials!.AccessKeyId,
          secretAccessKey: data.Credentials!.SecretAccessKey,
          sessionToken: data.Credentials!.SessionToken,
          region: region
        }); 
        //Call setState to enforce render being called again
        this.setState({
          infraReady: true, 
          kendra: kendra,
          s3: s3
        });
      }
    });
  }
  
  async componentDidMount() {
    Auth.configure(aws_exports);
    try {
      let credentials = await Auth.currentUserCredentials();
      //Use Cognito Credentials either authorized or unauthorized to get temporary credentials for Kendra
      this.setInfra(credentials.accessKeyId, credentials.secretAccessKey, credentials.sessionToken);
    } catch (e) {
      console.log("Auth exception: ", e);
    }
  }
  render() {
    return (
      <div className="App">
          <div style={{textAlign: 'center'}}>
              <img src={searchlogo} alt='Search Logo' />
          </div>
          <Search kendra={this.state.kendra} indexId={indexId} s3={this.state.s3} facetConfiguration={facetConfiguration}/>
      </div>
    );
  }
}

export default App;